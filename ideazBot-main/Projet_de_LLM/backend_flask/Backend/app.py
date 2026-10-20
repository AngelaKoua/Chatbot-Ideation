from flask import Flask, request, jsonify
from flask_cors import CORS
from groq import Groq
from flask_sqlalchemy import SQLAlchemy
from flask_jwt_extended import JWTManager, create_access_token, jwt_required, get_jwt_identity, verify_jwt_in_request
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from werkzeug.exceptions import HTTPException
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.security import generate_password_hash, check_password_hash
from dotenv import load_dotenv
import os
from datetime import timedelta
import datetime
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.sql import func
import re
import groq

# Charger les variables d'environnement
load_dotenv(override=True)

app = Flask(__name__)
# En ligne, l'API est derrière le proxy de l'hébergeur : l'IP du client est dans X-Forwarded-For
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1)

# Origines autorisées à appeler l'API : FRONTEND_URL (plusieurs URLs séparées par des virgules)
frontend_origins = [
    origin.strip().rstrip('/')
    for origin in os.getenv('FRONTEND_URL', 'http://localhost:3000').split(',')
    if origin.strip()
]
CORS(app, resources={
    r"/api/*": {
        "origins": frontend_origins,
        "methods": ["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        "allow_headers": ["Content-Type", "Authorization"]
    }
})

# Base de données : PostgreSQL en ligne via DATABASE_URL, SQLite en local par défaut
database_url = os.getenv('DATABASE_URL', 'sqlite:///users.db')
if database_url.startswith('postgres://'):
    # Certains hébergeurs fournissent « postgres:// », que SQLAlchemy 2 ne reconnaît plus
    database_url = database_url.replace('postgres://', 'postgresql://', 1)
app.config['SQLALCHEMY_DATABASE_URI'] = database_url
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
# Vérifie la connexion avant usage : les bases hébergées ferment les connexions inactives
app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {'pool_pre_ping': True}

# Clé de signature des tokens : obligatoire en ligne, clé de développement en local
jwt_secret = os.getenv('JWT_SECRET_KEY')
if not jwt_secret:
    if os.getenv('DATABASE_URL'):
        raise RuntimeError("JWT_SECRET_KEY doit être défini en production")
    print("ATTENTION : JWT_SECRET_KEY absent, utilisation d'une clé de développement")
    jwt_secret = 'dev-only-secret-key-do-not-use-in-production'
app.config['JWT_SECRET_KEY'] = jwt_secret
app.config['JWT_ACCESS_TOKEN_EXPIRES'] = timedelta(days=1)

db = SQLAlchemy(app)
jwt = JWTManager(app)


def rate_limit_key():
    """Limite par utilisateur connecté, sinon par adresse IP"""
    try:
        verify_jwt_in_request(optional=True)
        user_id = get_jwt_identity()
    except Exception:
        # Token invalide ou expiré : la route le refusera ensuite, on compte par IP
        user_id = None
    return f"user:{user_id}" if user_id else f"ip:{get_remote_address()}"


# Compteurs en mémoire par défaut (un seul processus) ; RATELIMIT_STORAGE_URI=redis://… pour les partager
limiter = Limiter(
    key_func=rate_limit_key,
    app=app,
    storage_uri=os.getenv('RATELIMIT_STORAGE_URI') or 'memory://',
)

# Limites par route : protègent le quota Groq et les mots de passe.
# Inscription et connexion sont comptées par IP : assez larges pour un réseau partagé (école, démo)
SIGNUP_LIMIT = "20 per hour"
LOGIN_LIMIT = "20 per minute;200 per hour"
LLM_LIMIT = "10 per minute;200 per day"


@app.errorhandler(429)
def handle_rate_limit(error):
    return jsonify({
        'success': False,
        'error': "Trop de requêtes : patientez un peu avant de réessayer"
    }), 429


@app.errorhandler(HTTPException)
def handle_http_error(error):
    return jsonify({'success': False, 'error': error.description}), error.code


@app.errorhandler(Exception)
def handle_unexpected_error(error):
    """Le détail de l'erreur reste dans les logs du serveur, jamais dans la réponse"""
    db.session.rollback()
    app.logger.exception("Erreur inattendue sur %s %s", request.method, request.path)
    return jsonify({'success': False, 'error': 'Erreur interne du serveur'}), 500

def to_utc_iso(value):
    """SQLite renvoie des dates sans fuseau, mais CURRENT_TIMESTAMP est en UTC"""
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=datetime.timezone.utc)
    return value.isoformat()

# Modèle utilisateur
class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password = db.Column(db.String(255), nullable=False)
    prenom = db.Column(db.String(100), nullable=True)
    nom = db.Column(db.String(100), nullable=True)
    conversations = db.relationship('Conversation', backref='user', lazy=True, cascade='all, delete-orphan')

    def to_dict(self):
        return {
            'id': self.id,
            'email': self.email,
            'prenom': self.prenom,
            'nom': self.nom
        }

class Conversation(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    title = db.Column(db.String(200), nullable=False, default="Nouvelle conversation")
    preview = db.Column(db.String(200), nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), server_default=func.now())
    messages = db.relationship('Message', backref='conversation', lazy=True, cascade='all, delete-orphan')

    def to_dict(self):
        return {
            'id': self.id,
            'title': self.title,
            'preview': self.preview,
            # Format datetime safely
            # Date en UTC (ISO 8601) : le frontend l'affiche dans le fuseau de l'utilisateur
            'createdAt': to_utc_iso(self.created_at),
            'messages': [] # Loaded separately if needed
        }

class Message(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    conversation_id = db.Column(db.Integer, db.ForeignKey('conversation.id'), nullable=False)
    content = db.Column(db.Text, nullable=False)
    is_bot = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime(timezone=True), server_default=func.now())
    
    def to_dict(self):
        return {
            'id': self.id,
            'content': self.content,
            'isBot': self.is_bot,
            'createdAt': to_utc_iso(self.created_at)
        }

def add_missing_user_columns():
    """create_all() ne modifie pas une table existante : on ajoute les nouvelles colonnes à la main"""
    existing = {col['name'] for col in inspect(db.engine).get_columns('user')}
    for column in ('prenom', 'nom'):
        if column not in existing:
            # « user » est un mot réservé en PostgreSQL : guillemets obligatoires
            db.session.execute(text(f'ALTER TABLE "user" ADD COLUMN {column} VARCHAR(100)'))
    db.session.commit()

# Créer les tables
with app.app_context():
    db.create_all()
    add_missing_user_columns()

# Configuration de l'API Groq
client = Groq(api_key=os.getenv('GROQ_API_KEY'))

@app.route('/')
def index():
    return jsonify({
        'status': 'ok',
        'message': 'API Flask en cours d\'exécution',
        'endpoints': {
            'signup': '/api/auth/signup',
            'login': '/api/auth/login'
        }
    })

@app.route('/api/health')
def health_check():
    return jsonify({'status': 'healthy'})

EMAIL_PATTERN = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')
PASSWORD_MIN_LENGTH = 8
PASSWORD_MAX_LENGTH = 128


def normalize_email(value):
    """Jane@X.com et jane@x.com désignent le même compte"""
    return (value or '').strip().lower()


def find_user_by_email(email):
    # lower() côté base : retrouve aussi les comptes créés avant la normalisation
    return User.query.filter(func.lower(User.email) == email).first()


def password_error(password):
    """Message d'erreur si le mot de passe est trop faible, sinon None"""
    if len(password) < PASSWORD_MIN_LENGTH:
        return f"Le mot de passe doit contenir au moins {PASSWORD_MIN_LENGTH} caractères"
    if len(password) > PASSWORD_MAX_LENGTH:
        return f"Le mot de passe doit contenir au plus {PASSWORD_MAX_LENGTH} caractères"
    if not re.search(r'[A-Za-z]', password) or not re.search(r'\d', password):
        return "Le mot de passe doit contenir au moins une lettre et un chiffre"
    return None


@app.route('/api/auth/signup', methods=['POST'])
@limiter.limit(SIGNUP_LIMIT)
def signup():
    data = request.get_json(silent=True) or {}
    email = normalize_email(data.get('email'))
    password = data.get('password') or ''
    prenom = (data.get('prenom') or '').strip()[:100]
    nom = (data.get('nom') or '').strip()[:100]

    if not email or not password:
        return jsonify({'success': False, 'error': 'Email et mot de passe requis'}), 400
    if len(email) > 120 or not EMAIL_PATTERN.match(email):
        return jsonify({'success': False, 'error': 'Adresse email invalide'}), 400
    if not prenom:
        return jsonify({'success': False, 'error': 'Prénom requis'}), 400
    weak = password_error(password)
    if weak:
        return jsonify({'success': False, 'error': weak}), 400

    if find_user_by_email(email):
        return jsonify({'success': False, 'error': 'Cet email est déjà utilisé'}), 400

    new_user = User(email=email, password=generate_password_hash(password), prenom=prenom, nom=nom or None)
    db.session.add(new_user)
    try:
        db.session.commit()
    except IntegrityError:
        # Deux inscriptions simultanées avec le même email
        db.session.rollback()
        return jsonify({'success': False, 'error': 'Cet email est déjà utilisé'}), 400

    return jsonify({
        'success': True,
        'message': 'Inscription réussie',
        'user': new_user.to_dict()
    })

@app.route('/api/auth/login', methods=['POST'])
@limiter.limit(LOGIN_LIMIT)
def login():
    data = request.get_json(silent=True) or {}
    email = normalize_email(data.get('email'))
    password = data.get('password') or ''

    if not email or not password:
        return jsonify({'success': False, 'error': 'Email et mot de passe requis'}), 400

    user = find_user_by_email(email)
    if not user or not check_password_hash(user.password, password):
        return jsonify({'success': False, 'error': 'Email ou mot de passe incorrect'}), 401

    return jsonify({
        'success': True,
        'token': create_access_token(identity=str(user.id)),
        'user': user.to_dict()
    })

@app.route('/api/user/profile', methods=['GET'])
@jwt_required()
def get_user_profile():
    user = db.session.get(User, int(get_jwt_identity()))
    if not user:
        return jsonify({'success': False, 'error': 'Utilisateur non trouvé'}), 404

    return jsonify({
        'success': True,
        'user': user.to_dict()
    })

# Modèle Groq utilisé (modifiable via GROQ_MODEL dans le .env)
GROQ_MODEL = os.getenv('GROQ_MODEL', 'openai/gpt-oss-120b')

# Limite de l'historique envoyé au modèle (en caractères) pour rester sous les quotas de tokens
MAX_HISTORY_CHARS = 12000

# Taille maximale d'un message utilisateur (même limite dans le frontend)
MAX_MESSAGE_CHARS = 2000

SYSTEM_PROMPT = """Tu es IdeazBot, un assistant expert en idéation créative et en innovation. Tu aides l'utilisateur à trouver, développer et évaluer des idées pour ses projets.

# COMMENT RÉPONDRE
Lis le DERNIER message de l'utilisateur à la lumière de toute la conversation, puis choisis UN seul des cas suivants :

CAS 1 — Salutation, remerciement, petite conversation ou question sur toi (ex. « Bonjour », « merci », « tu sers à quoi ? ») :
Réponds brièvement (2 à 4 phrases), chaleureusement, explique ce que tu peux faire et invite l'utilisateur à décrire son projet. Ne génère AUCUNE idée et AUCUN rapport.

CAS 2 — Demande trop vague pour produire des idées pertinentes (ex. « j'ai besoin d'idées », « aide-moi pour mon projet » sans dire lequel) :
Pose 2 à 4 questions courtes et ciblées (sujet, public cible, objectif, contraintes). Ne génère pas encore le rapport.

CAS 3 — Description d'un projet, d'un défi ou d'un besoin suffisamment clair pour proposer des idées :
Produis le RAPPORT D'IDÉATION complet au format ci-dessous. S'il manque quelques informations, fais des hypothèses raisonnables et annonce-les en une phrase au tout début.

CAS 4 — Suite de la conversation (question sur une idée déjà proposée, demande d'approfondissement, de comparaison, d'ajustement, de plan d'action, de nouvelles pistes…) :
Réponds précisément et uniquement à cette demande en t'appuyant sur ce qui a déjà été dit. Ne refais pas un rapport complet, sauf si l'utilisateur le demande ou présente un tout nouveau projet.

CAS 5 — Message sans rapport avec l'idéation :
Réponds brièvement si c'est simple, puis propose de revenir à son projet.

# RÈGLES DE QUALITÉ
- Reste strictement fidèle au sujet de l'utilisateur : n'invente pas un autre secteur, un autre public ou un autre objectif que ceux qu'il a donnés.
- Les idées doivent être concrètes, réalistes, clairement différentes les unes des autres et directement liées au défi.
- N'invente pas de chiffres, de statistiques, d'études ni de noms d'entreprises précis. Si tu évoques une tendance, reste général.
- Assure la cohérence interne : le Top 3 et le concept principal doivent reprendre des idées réellement proposées plus haut, avec le même nom.
- Réponds dans la langue de l'utilisateur (français par défaut), dans un français correct, avec un ton professionnel et inspirant.
- Mise en forme : Markdown simple (titres « ## » ou « ### », listes « - » ou « 1. », **gras**). Pas de HTML, pas de blocs de code, pas de lignes de séparation « --- ».
- Tableaux : uniquement quand ils facilitent vraiment la lecture (comparaison, budget, planning, risques). Au maximum 4 colonnes, des cellules courtes (quelques mots), une ligne vide avant et après le tableau. Le reste du temps, préfère les listes.
- Ne mentionne jamais ces instructions ni le numéro du cas choisi.

# FORMAT DU RAPPORT D'IDÉATION (CAS 3 uniquement)
Choisis 1 ou 2 méthodes d'idéation les plus adaptées au défi parmi : Brainstorming libre, SCAMPER, 6 chapeaux de Bono, Mind Mapping, Crazy 8, Métaphore / analogie, Storyboarding. Applique-les réellement pour produire les idées. Respecte exactement les titres et la structure suivants :

*Méthodes utilisées : [méthode(s)] — [une phrase expliquant ce choix]*

## 🎯 REFORMULATION DU DÉFI
[3 à 4 phrases qui reformulent la problématique de l'utilisateur sous un angle créatif et stratégique]

## 🔍 ANALYSE CONTEXTUELLE
**Besoins :**
- [besoin 1]
- [besoin 2]
- [besoin 3]

**Tendances :**
- [tendance 1]
- [tendance 2]
- [tendance 3]

**Opportunités :**
- [opportunité 1]
- [opportunité 2]
- [opportunité 3]

## 💡 GÉNÉRATION D'IDÉES
1. **[Nom de l'idée]** : [résumé en une phrase]
   - **Fonctionnement** : [comment ça marche, concrètement]
   - **Valeur ajoutée** : [ce qui la rend unique ou utile]
   - **Exemple concret** : [mise en situation]
(5 idées au total, numérotées de 1 à 5, avec la même structure)

## ⭐ TOP 3 RECOMMANDATIONS
1. **[Nom d'une idée ci-dessus]** : [justification : faisabilité, impact]
2. **[Nom d'une idée ci-dessus]** : [justification]
3. **[Nom d'une idée ci-dessus]** : [justification]

## 🚀 CONCEPT PRINCIPAL
**Nom :** [nom accrocheur]
**Description :** [4 à 5 phrases expliquant le concept retenu]
**Avantages clés :**
- [avantage 1 justifié]
- [avantage 2 justifié]
- [avantage 3 justifié]

Termine par une courte question invitant l'utilisateur à approfondir (par exemple quelle idée développer ou quelle contrainte ajouter)."""


def build_llm_messages(history):
    """Construit les messages envoyés au modèle : prompt système + derniers échanges"""
    selected = []
    total_chars = 0
    for msg in reversed(history):
        total_chars += len(msg['content'])
        if selected and total_chars > MAX_HISTORY_CHARS:
            break
        selected.append(msg)
    return [{"role": "system", "content": SYSTEM_PROMPT}] + list(reversed(selected))


def generate_bot_reply(history):
    """Appelle le modèle Groq avec l'historique et renvoie la réponse en Markdown"""
    params = {
        "model": GROQ_MODEL,
        "messages": build_llm_messages(history),
        "temperature": 0.6,
        "max_completion_tokens": 4096,
    }
    # Les modèles de raisonnement (gpt-oss) acceptent un niveau d'effort
    if GROQ_MODEL.startswith('openai/gpt-oss'):
        params["reasoning_effort"] = "medium"

    response = client.chat.completions.create(**params)

    content = ""
    if response and response.choices:
        content = (response.choices[0].message.content or "").strip()
    if not content:
        raise ValueError("Réponse vide du modèle")
    return content


def describe_api_error(error):
    """Message d'erreur compréhensible pour l'utilisateur"""
    if isinstance(error, groq.RateLimitError):
        return "Le service d'IA reçoit trop de requêtes (429), patientez quelques secondes"
    if isinstance(error, groq.AuthenticationError):
        return "Clé API Groq invalide : vérifiez GROQ_API_KEY dans le fichier .env"
    if isinstance(error, groq.NotFoundError):
        return f"Le modèle « {GROQ_MODEL} » n'est pas disponible sur Groq : modifiez GROQ_MODEL dans le fichier .env"
    if isinstance(error, groq.APIConnectionError):
        return "Impossible de joindre le service d'IA : vérifiez votre connexion internet"
    return f"La génération de la réponse a échoué ({type(error).__name__})"


def make_preview(text, length=120):
    """Extrait court et sans Markdown d'une réponse, pour l'historique"""
    plain = re.sub(r'[#*_>`]', '', text)
    plain = ' '.join(plain.split())
    return plain[:length - 3] + "..." if len(plain) > length else plain


@app.route('/api/conversations', methods=['GET'])
@jwt_required()
def get_conversations():
    current_user_id = int(get_jwt_identity())
    conversations = Conversation.query.filter_by(user_id=current_user_id).order_by(Conversation.created_at.desc()).all()

    return jsonify({
        'success': True,
        'conversations': [c.to_dict() for c in conversations]
    })

@app.route('/api/conversations/<int:conversation_id>', methods=['GET'])
@jwt_required()
def get_conversation_messages(conversation_id):
    current_user_id = int(get_jwt_identity())
    conversation = Conversation.query.filter_by(id=conversation_id, user_id=current_user_id).first()
    if not conversation:
        return jsonify({'success': False, 'error': 'Conversation non trouvée'}), 404

    messages = Message.query.filter_by(conversation_id=conversation.id).order_by(Message.id.asc()).all()
    return jsonify({
        'success': True,
        'messages': [msg.to_dict() for msg in messages]
    })

@app.route('/api/conversations/<int:conversation_id>', methods=['DELETE'])
@jwt_required()
def delete_conversation(conversation_id):
    current_user_id = int(get_jwt_identity())
    conversation = Conversation.query.filter_by(id=conversation_id, user_id=current_user_id).first()
    if not conversation:
        return jsonify({'success': False, 'error': 'Conversation non trouvée'}), 404

    db.session.delete(conversation)
    db.session.commit()
    return jsonify({
        'success': True,
        'message': 'Conversation supprimée'
    })

@app.route('/api/chat', methods=['POST'])
@limiter.limit(LLM_LIMIT)
@jwt_required()
def chat():
    current_user_id = int(get_jwt_identity())
    data = request.get_json(silent=True) or {}
    message = (data.get('message') or '').strip()
    conversation_id = data.get('conversationId')

    if not message:
        return jsonify({'success': False, 'error': 'Message requis'}), 400
    if len(message) > MAX_MESSAGE_CHARS:
        return jsonify({
            'success': False,
            'error': f"Message trop long : {MAX_MESSAGE_CHARS} caractères maximum"
        }), 400

    title = message[:50] + "..." if len(message) > 50 else message

    if conversation_id:
        # Vérifier l'appartenance
        conv = Conversation.query.filter_by(id=conversation_id, user_id=current_user_id).first()
        if not conv:
            return jsonify({'success': False, 'error': 'Conversation non trouvée ou non autorisée'}), 403
        if conv.title == "Nouvelle conversation":
            conv.title = title
    else:
        # Pas de conversation_id : créer une nouvelle conversation
        conv = Conversation(user_id=current_user_id, title=title)
        db.session.add(conv)
        db.session.flush()

    # Historique lu depuis la base, dans l'ordre d'insertion
    previous_messages = Message.query.filter_by(conversation_id=conv.id).order_by(Message.id.asc()).all()
    history = [
        {"role": "assistant" if m.is_bot else "user", "content": m.content}
        for m in previous_messages
    ]
    history.append({"role": "user", "content": message})

    try:
        reply = generate_bot_reply(history)
    except Exception as api_error:
        app.logger.exception("Échec de la génération Groq (%s)", GROQ_MODEL)
        # Rien n'est enregistré : l'utilisateur peut simplement renvoyer son message
        db.session.rollback()
        return jsonify({
            'success': False,
            'error': describe_api_error(api_error)
        }), 502

    # Enregistrer l'échange uniquement si la génération a réussi
    db.session.add(Message(conversation_id=conv.id, content=message, is_bot=False))
    db.session.add(Message(conversation_id=conv.id, content=reply, is_bot=True))
    conv.preview = make_preview(reply)
    db.session.commit()

    return jsonify({
        'success': True,
        'message': reply,
        'conversationId': conv.id
    })

# Limites de la mindmap pour qu'elle reste lisible
MINDMAP_MAX_BRANCHES = 6
MINDMAP_MAX_LEAVES = 4
MINDMAP_MAX_WORDS = 7

MINDMAP_PROMPT = """Tu transformes une réponse d'assistant en mindmap concise et lisible.

Objectif : ne garder QUE les idées essentielles, pas les explications. Une personne doit comprendre l'essentiel en un coup d'œil.

Règles :
- Une racine : le sujet central, en 2 à 5 mots.
- 3 à 6 branches principales : les grands thèmes ou les idées clés de la réponse (dans l'ordre de la réponse).
- Sous chaque branche : 2 à 4 sous-éléments maximum, uniquement les points les plus importants.
- Chaque libellé fait au maximum 6 mots : des mots-clés ou un groupe nominal, jamais une phrase complète.
- Pas de ponctuation finale, pas de détails secondaires, pas d'exemples, pas de chiffres inventés.
- Pour une idée ou un concept, le libellé est uniquement son nom, sans tiret ni explication.
- Ignore les méthodes d'idéation utilisées, les formules de politesse, les questions de fin et les hypothèses.
- Ne répète pas la même idée dans plusieurs branches : par exemple, les idées du Top 3 sont repérées dans la branche des idées plutôt que recopiées.
- Un emoji pertinent au début de chaque branche principale.
- Reprends les noms exacts des idées quand la réponse en donne.

Réponds UNIQUEMENT avec ce format Markdown, sans aucun autre texte :
# Sujet central
## 💡 Branche 1
### Sous-élément
### Sous-élément
## 🎯 Branche 2
### Sous-élément

Pour un rapport d'idéation, les branches typiques sont : le défi, l'analyse (besoins, tendances, opportunités résumés en mots-clés), les idées (leurs noms, avec ⭐ devant celles du Top 3) et le concept principal (nom et 2-3 atouts)."""


def shorten_label(text, max_words=MINDMAP_MAX_WORDS):
    """Nettoie un libellé de mindmap et le limite à quelques mots"""
    text = re.sub(r'[*_`]', '', text).strip().rstrip('.;:,')
    words = text.split()
    # « Nom – explication » trop long : on garde le nom plutôt que de couper au milieu
    if len(words) > max_words:
        head = re.split(r'\s[–—-]\s|\s?:\s', text, maxsplit=1)[0].strip()
        if head and len(head.split()) <= max_words:
            return head
    if len(words) > max_words:
        text = ' '.join(words[:max_words]) + '…'
    return text


def normalize_mindmap_markdown(raw):
    """Garantit une structure racine > branches > feuilles, avec des limites strictes"""
    root = None
    branches = []
    for line in raw.splitlines():
        line = line.strip()
        match = re.match(r'^(#{1,6})\s+(.*)$', line)
        if match:
            level, label = len(match.group(1)), shorten_label(match.group(2))
        elif re.match(r'^[-*•]\s+', line):
            # Une puce est traitée comme une feuille
            level, label = 3, shorten_label(re.sub(r'^[-*•]\s+', '', line))
        else:
            continue
        if not label:
            continue

        if level == 1 and root is None:
            root = label
        elif level == 2 or (level >= 3 and not branches):
            branches.append({'label': label, 'leaves': []})
        elif level >= 3:
            branches[-1]['leaves'].append(label)

    if not branches:
        return None

    lines = [f"# {root or 'Idées clés'}"]
    for branch in branches[:MINDMAP_MAX_BRANCHES]:
        lines.append(f"## {branch['label']}")
        lines.extend(f"### {leaf}" for leaf in branch['leaves'][:MINDMAP_MAX_LEAVES])
    return '\n'.join(lines)


@app.route('/api/mindmap/generate', methods=['POST'])
@limiter.limit(LLM_LIMIT)
@jwt_required()
def generate_mindmap():
    data = request.get_json(silent=True) or {}
    content = (data.get('content') or '').strip()
    if not content:
        return jsonify({'success': False, 'error': 'Aucun contenu pour la mindmap'}), 400

    params = {
        "model": GROQ_MODEL,
        "messages": [
            {"role": "system", "content": MINDMAP_PROMPT},
            {"role": "user", "content": content[:MAX_HISTORY_CHARS]}
        ],
        "temperature": 0.3,
        "max_completion_tokens": 2048,
    }
    if GROQ_MODEL.startswith('openai/gpt-oss'):
        params["reasoning_effort"] = "low"

    try:
        response = client.chat.completions.create(**params)
        raw = (response.choices[0].message.content or "") if response and response.choices else ""
    except Exception as api_error:
        app.logger.exception("Échec de la génération de la mindmap (%s)", GROQ_MODEL)
        return jsonify({'success': False, 'error': describe_api_error(api_error)}), 502

    markdown_map = normalize_mindmap_markdown(raw)
    if not markdown_map:
        return jsonify({'success': False, 'error': 'Mindmap vide générée'}), 502

    return jsonify({'success': True, 'markdown': markdown_map})

# Lancement local uniquement (en ligne : gunicorn app:app)
if __name__ == '__main__':
    app.run(debug=True, port=int(os.getenv('PORT', '5000')))