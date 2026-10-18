from flask import Flask, request, jsonify, send_file
from flask_cors import CORS
from groq import Groq
from flask_sqlalchemy import SQLAlchemy
from flask_jwt_extended import JWTManager, create_access_token, jwt_required, get_jwt_identity
from werkzeug.security import generate_password_hash, check_password_hash
from dotenv import load_dotenv
import os
from datetime import timedelta
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import markdown
import datetime
from sqlalchemy.sql import func
import re
import traceback
import groq

# Charger les variables d'environnement
load_dotenv(override=True)

app = Flask(__name__)

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
    conversations = db.relationship('Conversation', backref='user', lazy=True, cascade='all, delete-orphan')

    def to_dict(self):
        return {
            'id': self.id,
            'email': self.email
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

# Créer les tables
with app.app_context():
    db.create_all()

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

@app.route('/api/auth/signup', methods=['POST'])
def signup():
    try:
        data = request.get_json()
        if not data:
            return jsonify({'error': 'Aucune donnée reçue'}), 400
            
        email = data.get('email')
        password = data.get('password')

        if not email or not password:
            return jsonify({'error': 'Email et mot de passe requis'}), 400

        # Vérifier si l'utilisateur existe déjà
        if User.query.filter_by(email=email).first():
            return jsonify({'error': 'Cet email est déjà utilisé'}), 400

        # Créer un nouvel utilisateur
        hashed_password = generate_password_hash(password)
        new_user = User(email=email, password=hashed_password)
        db.session.add(new_user)
        db.session.commit()

        return jsonify({
            'success': True,
            'message': 'Inscription réussie',
            'user': new_user.to_dict()
        })

    except Exception as e:
        db.session.rollback()
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@app.route('/api/auth/login', methods=['POST'])
def login():
    try:
        data = request.get_json()
        if not data:
            return jsonify({'error': 'Aucune donnée reçue'}), 400
            
        email = data.get('email')
        password = data.get('password')

        if not email or not password:
            return jsonify({'error': 'Email et mot de passe requis'}), 400

        # Vérifier les identifiants
        user = User.query.filter_by(email=email).first()
        if not user or not check_password_hash(user.password, password):
            return jsonify({'error': 'Email ou mot de passe incorrect'}), 401

        # Créer le token JWT
        access_token = create_access_token(identity=str(user.id))
        
        return jsonify({
            'success': True,
            'token': access_token,
            'user': user.to_dict()
        })

    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

# Route protégée pour tester l'authentification
@app.route('/api/user/profile', methods=['GET'])
@jwt_required()
def get_user_profile():
    try:
        current_user_id = int(get_jwt_identity())
        user = User.query.get(current_user_id)
        
        if not user:
            return jsonify({'error': 'Utilisateur non trouvé'}), 404
            
        return jsonify({
            'success': True,
            'user': user.to_dict()
        })
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

# Modèle Groq utilisé (modifiable via GROQ_MODEL dans le .env)
GROQ_MODEL = os.getenv('GROQ_MODEL', 'openai/gpt-oss-120b')

# Limite de l'historique envoyé au modèle (en caractères) pour rester sous les quotas de tokens
MAX_HISTORY_CHARS = 12000

WELCOME_MESSAGE = (
    "Bonjour ! Je suis IdeazBot, votre assistant en idéation créative. "
    "Parlez-moi de votre projet, de votre défi ou de votre besoin, et je vous aiderai "
    "à générer, approfondir et évaluer des idées adaptées."
)

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


def markdown_to_html_document(markdown_text):
    """Convertit du Markdown en page HTML simple, lisible dans un client mail"""
    html = markdown.markdown(markdown_text, extensions=['tables'])
    return f"""<html>
<head>
    <meta charset="utf-8">
    <style>
        body {{ font-family: Arial, sans-serif; max-width: 800px; margin: 0 auto; padding: 20px; color: #1f2937; }}
        h1, h2, h3 {{ color: #6d28d9; }}
        table {{ border-collapse: collapse; width: 100%; }}
        th, td {{ border: 1px solid #ddd6fe; padding: 6px 10px; text-align: left; }}
        th {{ background: #ede9fe; }}
    </style>
</head>
<body>
{html}
</body>
</html>"""


class EmailConfigError(Exception):
    """Les identifiants SMTP ne sont pas définis dans le .env"""


def send_email_smtp(to_email, subject, content, format='html'):
    """Envoie un email via SMTP (identifiants lus dans le .env)"""
    smtp_server = os.getenv('SMTP_SERVER', 'smtp.gmail.com')
    smtp_port = int(os.getenv('SMTP_PORT', '587'))
    email_user = os.getenv('EMAIL_USER')
    email_password = os.getenv('EMAIL_PASSWORD')

    if not email_user or not email_password:
        raise EmailConfigError(
            "Envoi d'email non configuré : ajoutez EMAIL_USER et EMAIL_PASSWORD "
            "(mot de passe d'application) dans le fichier .env"
        )

    msg = MIMEMultipart('alternative')
    msg['From'] = email_user
    msg['To'] = to_email
    msg['Subject'] = subject
    # Version texte toujours présente, version HTML en plus si demandée
    msg.attach(MIMEText(content, 'plain', 'utf-8'))
    if format == 'html':
        msg.attach(MIMEText(markdown_to_html_document(content), 'html', 'utf-8'))

    with smtplib.SMTP(smtp_server, smtp_port, timeout=20) as server:
        server.starttls()
        server.login(email_user, email_password)
        server.send_message(msg)


@app.route('/api/conversations', methods=['GET'])
@jwt_required()
def get_conversations():
    try:
        current_user_id = int(get_jwt_identity())
        conversations = Conversation.query.filter_by(user_id=current_user_id).order_by(Conversation.created_at.desc()).all()
        
        return jsonify({
            'success': True,
            'conversations': [c.to_dict() for c in conversations]
        })
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@app.route('/api/conversations/<int:conversation_id>', methods=['GET'])
@jwt_required()
def get_conversation_messages(conversation_id):
    try:
        current_user_id = int(get_jwt_identity())
        conversation = Conversation.query.filter_by(id=conversation_id, user_id=current_user_id).first()
        
        if not conversation:
            return jsonify({'error': 'Conversation non trouvée'}), 404
            
        messages = Message.query.filter_by(conversation_id=conversation.id).order_by(Message.id.asc()).all()
        
        # Formater comme attendu par le frontend
        formatted_messages = []
        for msg in messages:
            formatted_messages.append(msg.to_dict())
            
        return jsonify({
            'success': True,
            'messages': formatted_messages
        })
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@app.route('/api/conversations/<int:conversation_id>', methods=['DELETE'])
@jwt_required()
def delete_conversation(conversation_id):
    try:
        current_user_id = int(get_jwt_identity())
        conversation = Conversation.query.filter_by(id=conversation_id, user_id=current_user_id).first()
        
        if not conversation:
            return jsonify({'error': 'Conversation non trouvée'}), 404
            
        db.session.delete(conversation)
        db.session.commit()
        
            
        return jsonify({
            'success': True,
            'message': 'Conversation supprimée'
        })
    except Exception as e:
        db.session.rollback()
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@app.route('/api/chat/start', methods=['POST'])
@jwt_required()
def start_chat():
    try:
        current_user_id = int(get_jwt_identity())
        
        # Créer une nouvelle conversation en base de données
        new_conv = Conversation(user_id=current_user_id, title="Nouvelle conversation")
        db.session.add(new_conv)
        db.session.commit()
        
        
        welcome_message = WELCOME_MESSAGE
        
        # Sauvegarder le message de bienvenue (bot)
        bot_msg = Message(conversation_id=new_conv.id, content=welcome_message, is_bot=True)
        db.session.add(bot_msg)
        db.session.commit()
        
        return jsonify({
            'success': True,
            'conversationId': new_conv.id,
            'message': welcome_message
        })
    except Exception as e:
        db.session.rollback()
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@app.route('/api/chat', methods=['POST'])
@jwt_required()
def chat():
    try:
        current_user_id = int(get_jwt_identity())
        data = request.get_json(silent=True) or {}
        message = (data.get('message') or '').strip()
        conversation_id = data.get('conversationId')

        if not message:
            return jsonify({'error': 'Message requis'}), 400

        title = message[:50] + "..." if len(message) > 50 else message

        if conversation_id:
            # Vérifier l'appartenance
            conv = Conversation.query.filter_by(id=conversation_id, user_id=current_user_id).first()
            if not conv:
                return jsonify({'error': 'Conversation non trouvée ou non autorisée'}), 403
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
            print("=" * 50)
            print(f"ERREUR LORS DE LA GÉNÉRATION GROQ ({GROQ_MODEL}):")
            traceback.print_exc()
            print("=" * 50)
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

    except Exception as e:
        db.session.rollback()
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

# Route pour réinitialiser une session d'idéation (On ne supprime plus via ce bouton, mais via le DELETE en frontend)
@app.route('/api/chat/reset', methods=['POST'])
@jwt_required()
def reset_chat():
    try:
        # Simplification: l'utilisateur front demandera une nouvelle conversation,
        # donc on a juste besoin de ne rien faire ici sauf valider
        return jsonify({
            'success': True,
            'message': 'Veuillez utiliser start_chat ou envoyer un message sans ID pour démarrer une nouvelle conversation.'
        })
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)

        }), 500

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
@jwt_required()
def generate_mindmap():
    try:
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
            traceback.print_exc()
            return jsonify({'success': False, 'error': describe_api_error(api_error)}), 502

        markdown_map = normalize_mindmap_markdown(raw)
        if not markdown_map:
            return jsonify({'success': False, 'error': 'Mindmap vide générée'}), 502

        return jsonify({'success': True, 'markdown': markdown_map})

    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

# Ajouter les nouvelles routes pour l'export et l'envoi par email
@app.route('/api/mindmap/export', methods=['POST'])
@jwt_required()
def export_mindmap():
    try:
        current_user_id = int(get_jwt_identity())
        data = request.json
        format = data.get('format', 'markdown')
        content = data.get('content')
        
        if not content:
            return jsonify({'error': 'Aucun contenu à exporter'}), 400
            
        # Pour le format markdown, on renvoie directement le contenu
        if format == 'markdown':
            return jsonify({
                'success': True,
                'content': content,
                'format': format
            })
            
        # Pour le format HTML, on convertit le markdown en HTML
        elif format == 'html':
            html_content = markdown.markdown(content)
            styled_html = f"""
            <html>
            <head>
                <style>
                    body {{ font-family: Arial, sans-serif; max-width: 800px; margin: 0 auto; padding: 20px; }}
                    h1 {{ color: #2c3e50; }}
                    .section {{ margin: 20px 0; padding: 10px; border-left: 4px solid #3498db; }}
                    .emoji {{ font-size: 1.5em; }}
                </style>
            </head>
            <body>
                {html_content}
            </body>
            </html>
            """
            return jsonify({
                'success': True,
                'content': styled_html,
                'format': 'html'
            })
            
        if format == 'png':
            return send_file(
                content,
                mimetype='image/png',
                as_attachment=True,
                download_name='mindmap.png'
            )
            
        return jsonify({
            'success': True,
            'content': content,
            'format': format
        })
        
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@app.route('/api/mindmap/send-email', methods=['POST'])
@jwt_required()
def send_mindmap_email():
    """Envoie par email soit le contenu fourni (ex. la mindmap),
    soit la dernière réponse du bot d'une conversation de l'utilisateur"""
    try:
        current_user_id = int(get_jwt_identity())
        data = request.get_json(silent=True) or {}
        to_email = (data.get('email') or '').strip()
        format = data.get('format', 'html')
        content = (data.get('content') or '').strip()
        conversation_id = data.get('conversationId')

        if not to_email or not re.match(r'^[^@\s]+@[^@\s]+\.[^@\s]+$', to_email):
            return jsonify({'success': False, 'error': 'Adresse email valide requise'}), 400

        if not content:
            if not conversation_id:
                return jsonify({'success': False, 'error': 'Fournir "content" ou "conversationId"'}), 400
            conversation = Conversation.query.filter_by(id=conversation_id, user_id=current_user_id).first()
            if not conversation:
                return jsonify({'success': False, 'error': 'Conversation non trouvée'}), 404
            last_bot_message = (Message.query
                                .filter_by(conversation_id=conversation.id, is_bot=True)
                                .order_by(Message.id.desc())
                                .first())
            if not last_bot_message:
                return jsonify({'success': False, 'error': 'Aucune réponse à envoyer dans cette conversation'}), 404
            content = last_bot_message.content

        send_email_smtp(to_email, "Votre Mindmap d'Idéation - IdeazBot", content, format)

        return jsonify({
            'success': True,
            'message': 'Email envoyé avec succès'
        })

    except EmailConfigError as e:
        return jsonify({'success': False, 'error': str(e)}), 503
    except smtplib.SMTPAuthenticationError:
        return jsonify({'success': False, 'error': 'Identifiants SMTP refusés : vérifiez EMAIL_USER et EMAIL_PASSWORD'}), 502
    except Exception as e:
        traceback.print_exc()
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

# Lancement local uniquement (en ligne : gunicorn app:app)
if __name__ == '__main__':
    app.run(debug=True, port=int(os.getenv('PORT', '5000')))