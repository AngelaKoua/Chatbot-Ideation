'use client';
import { useState, useRef, useEffect } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import {
  Brain,
  Lightbulb,
  Send,
  Sparkles,
  Bot,
  User,
  Rocket,
  Zap,
  History,
  Download,
  Plus,
  MessageCircle,
  X,
  Search,
  Trash2,
  LogOut
} from "lucide-react";
import { UserData, Message, Conversation, FloatingHistoryPanelProps, ChatMessageProps } from '../types';
import { API_URL, MAX_MESSAGE_LENGTH, redirectToLogin, isAuthError } from '../services/api';

// Positions fixes (pas de Math.random) : le rendu serveur et le rendu navigateur sont identiques
const PARTICLES = [...Array(8)].map((_, i) => ({
  left: (i * 47 + 13) % 90 + 5,
  top: (i * 37 + 23) % 90 + 5,
  delay: (i * 0.7) % 3,
  duration: 3 + (i % 3) * 0.7
}));

// Composant d'arrière-plan animé
const AnimatedBackground = () => {
  return (
    <div className="fixed inset-0 overflow-hidden pointer-events-none">
      {/* Lumières flottantes */}
      <div className="absolute w-32 h-32 rounded-full top-20 left-10 bg-emerald-300/20 blur-xl animate-pulse"></div>
      <div className="absolute w-24 h-24 rounded-full top-40 right-20 bg-lime-300/20 blur-xl animate-pulse" style={{ animationDelay: '1s' }}></div>
      <div className="absolute w-40 h-40 rounded-full bottom-32 left-1/4 bg-yellow-300/15 blur-xl animate-pulse" style={{ animationDelay: '2s' }}></div>
      <div className="absolute w-20 h-20 rounded-full top-1/3 right-1/3 bg-emerald-400/25 blur-lg animate-pulse" style={{ animationDelay: '0.5s' }}></div>

      {/* Cerveaux flottants */}
      <div className="absolute top-1/4 left-1/2 opacity-5 animate-float">
        <Brain className="w-16 h-16 text-emerald-600 transform rotate-12" />
      </div>
      <div className="absolute bottom-1/4 right-1/4 opacity-5 animate-float" style={{ animationDelay: '1.5s' }}>
        <Brain className="w-12 h-12 text-lime-600 transform -rotate-12" />
      </div>
      <div className="absolute top-1/2 left-1/4 opacity-5 animate-float" style={{ animationDelay: '0.8s' }}>
        <Lightbulb className="text-lime-600 transform rotate-45 w-14 h-14" />
      </div>

      {/* Particules flottantes */}
      {PARTICLES.map((particle, i) => (
        <div
          key={i}
          className="absolute w-2 h-2 rounded-full bg-gradient-to-r from-emerald-400 to-lime-400 opacity-20 animate-float"
          style={{
            left: `${particle.left}%`,
            top: `${particle.top}%`,
            animationDelay: `${particle.delay}s`,
            animationDuration: `${particle.duration}s`
          }}
        />
      ))}
    </div>
  );
};

// Le backend renvoie des dates UTC : on les affiche dans le fuseau du navigateur
const formatTime = (iso?: string) =>
  iso ? new Date(iso).toLocaleTimeString('fr-FR', { hour: '2-digit', minute: '2-digit' }) : '';

const formatDateTime = (iso?: string) =>
  iso
    ? new Date(iso).toLocaleString('fr-FR', { day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit' })
    : 'Récemment';

// Hook pour gérer l'authentification utilisateur
const useAuth = () => {
  const [user, setUser] = useState<UserData | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const fetchUserData = async () => {
      const token = localStorage.getItem('authToken');
      if (!token) {
        redirectToLogin();
        return;
      }

      try {
        const toUserData = (data: any): UserData => ({
          nom: data?.nom || data?.email?.split('@')[0] || "Utilisateur",
          prenom: data?.prenom || "",
          email: data?.email || "",
          id: data?.id || 0
        });

        // Affichage immédiat avec les infos enregistrées à la connexion
        const storedUser = sessionStorage.getItem('userInfo');
        setUser(toUserData(storedUser ? JSON.parse(storedUser) : null));

        // Puis profil à jour depuis le serveur (prénom inclus, même pour une session ouverte avant)
        try {
          const res = await fetch(`${API_URL}/api/user/profile`, {
            headers: { Authorization: `Bearer ${token}` }
          });
          if (isAuthError(res.status)) {
            redirectToLogin();
            return;
          }
          const data = await res.json();
          if (res.ok && data.success && data.user) {
            setUser(toUserData(data.user));
            sessionStorage.setItem('userInfo', JSON.stringify({ ...data.user, loginTime: new Date().toISOString() }));
          }
        } catch (profileError) {
          console.error('Profil indisponible:', profileError);
        }
      } catch (error) {
        console.error('Erreur d\'authentification:', error);
        setUser(null);
      } finally {
        setLoading(false);
      }
    };

    fetchUserData();
  }, []);

  return { user, loading };
};

// FloatingHistoryPanel component
const FloatingHistoryPanel: React.FC<FloatingHistoryPanelProps> = ({
  isOpen,
  onClose,
  conversations,
  onSelectConversation,
  onDeleteConversation,
  onNewConversation
}) => {
  const [searchTerm, setSearchTerm] = useState('');

  const filteredConversations = conversations.filter((conv: Conversation) => {
    const searchLower = searchTerm.toLowerCase();
    const matchesTitle = conv.title.toLowerCase().includes(searchLower);
    const matchesPreview = conv.preview && conv.preview.toLowerCase().includes(searchLower);
    const matchesMessages = conv.messages && conv.messages.some(msg => msg.content.toLowerCase().includes(searchLower));
    
    return matchesTitle || matchesPreview || matchesMessages;
  });

  return (
    <>
    {/* Fond assombri sur mobile : un toucher en dehors ferme le panneau */}
    {isOpen && (
      <div className="fixed inset-0 z-40 bg-black/30 sm:hidden" onClick={onClose} aria-hidden="true" />
    )}
    {/* Plein écran sur mobile, panneau flottant à droite à partir de sm */}
    <div className={`fixed inset-y-0 right-0 w-full sm:inset-y-4 sm:right-4 sm:w-80 flex flex-col bg-white/95 backdrop-blur-xl sm:rounded-3xl shadow-2xl border border-emerald-200/30 transform transition-all duration-500 z-50 ${isOpen ? 'translate-x-0 opacity-100' : 'translate-x-full opacity-0 pointer-events-none'
      }`}>

      {/* Header avec couleurs IdeazBot */}
      <div className="flex items-center justify-between p-4 border-b sm:p-6 border-emerald-100/50">
        <div className="flex items-center space-x-3">
          <div className="w-2 h-2 rounded-full bg-gradient-to-r from-emerald-500 to-lime-500 animate-pulse"></div>
          <h3 className="font-semibold text-gray-800">Historique</h3>
        </div>
        <button
          onClick={onClose}
          className="p-2 text-gray-400 transition-colors rounded-lg hover:text-emerald-600 hover:bg-emerald-50"
          aria-label="Fermer l'historique"
        >
          <X className="w-5 h-5" />
        </button>
      </div>

      {/* Search bar */}
      <div className="p-4 border-b border-emerald-100/50">
        <div className="relative">
          <Search className="absolute w-4 h-4 text-emerald-400 transform -translate-y-1/2 left-3 top-1/2" />
          <input
            type="text"
            placeholder="Rechercher une conversation..."
            value={searchTerm}
            onChange={(e) => setSearchTerm(e.target.value)}
            className="w-full py-2 pl-10 pr-4 text-sm border-0 outline-none bg-gradient-to-r from-emerald-50 to-lime-50 rounded-xl focus:ring-2 focus:ring-emerald-500/20"
          />
        </div>
      </div>

      {/* New conversation button */}
      <div className="p-4">
        <button
          onClick={() => {
            onNewConversation();
            onClose();
          }}
          className="flex items-center justify-center w-full p-3 space-x-2 text-sm font-medium text-white transition-all duration-200 shadow-lg bg-gradient-to-r from-emerald-600 to-lime-600 hover:from-emerald-700 hover:to-lime-700 rounded-xl hover:shadow-xl"
        >
          <Plus className="w-4 h-4" />
          <span>Nouvelle conversation</span>
        </button>
      </div>

      {/* Conversations list */}
      <div className="flex-1 min-h-0 px-4 pb-[max(1rem,env(safe-area-inset-bottom))] overflow-y-auto">
        {filteredConversations.length === 0 ? (
          <div className="py-8 text-center text-gray-400">
            <MessageCircle className="w-8 h-8 mx-auto mb-2 opacity-30" />
            <p className="text-sm">Aucune conversation trouvée</p>
          </div>
        ) : (
          <div className="space-y-2">
            {filteredConversations.map((conv) => (
              <div
                key={conv.id}
                onClick={() => {
                  onSelectConversation(conv);
                  onClose();
                }}
                className="p-3 transition-all duration-200 border border-transparent cursor-pointer group rounded-xl hover:bg-gradient-to-r hover:from-emerald-50 hover:to-lime-50 hover:border-emerald-200/30"
              >
                <div className="flex items-start justify-between">
                  <div className="flex-1 min-w-0">
                    <p className="text-sm font-medium text-gray-800 truncate">
                      {conv.title}
                    </p>
                    <p className="mt-1 text-xs text-gray-500 truncate">
                      {conv.preview}
                    </p>
                    <p className="mt-1 text-xs text-emerald-400">{conv.timestamp}</p>
                  </div>
                  <button
                    onClick={(e) => {
                      e.stopPropagation();
                      onDeleteConversation(conv.id);
                    }}
                    /* Visible en permanence sur écran tactile, au survol avec une souris */
                    className="p-2 -m-1 text-gray-400 transition-all duration-200 [@media(hover:hover)]:opacity-0 [@media(hover:hover)]:group-hover:opacity-100 hover:text-red-500"
                    aria-label="Supprimer la conversation"
                  >
                    <Trash2 className="w-4 h-4" />
                  </button>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
    </>
  );
};

const ChatMessage: React.FC<ChatMessageProps> = ({ message, isBot, onGenerateMindmap }) => {
  return (
    <div className={`flex ${isBot ? 'justify-start' : 'justify-end'} mb-4 sm:mb-6`}>
      <div className={`flex items-start gap-2 sm:gap-3 min-w-0 max-w-full sm:max-w-[85%] lg:max-w-[80%] ${!isBot ? 'flex-row-reverse' : ''}`}>
        {/* Avatar */}
        <div className={`w-8 h-8 sm:w-10 sm:h-10 rounded-full flex items-center justify-center shadow-lg flex-shrink-0 ${isBot
          ? 'bg-gradient-to-r from-emerald-600 to-lime-600'
          : 'bg-gradient-to-r from-gray-600 to-gray-700'
          }`}>
          {isBot ? (
            <Brain className="w-4 h-4 text-white sm:w-5 sm:h-5" />
          ) : (
            <User className="w-4 h-4 text-white sm:w-5 sm:h-5" />
          )}
        </div>

        {/* Message content : les mots très longs (liens…) passent à la ligne au lieu de déborder */}
        <div className={`p-3 sm:p-4 rounded-2xl shadow-sm border max-w-full min-w-0 [overflow-wrap:anywhere] ${isBot
          ? 'bg-gradient-to-br from-emerald-50 to-lime-50 border-emerald-100/50'
          : 'bg-gradient-to-br from-gray-100 to-gray-50 border-gray-200/50'
          }`}>
          <div className="mb-2 text-sm font-medium text-gray-600">
            {isBot ? 'IdeazBot' : 'Vous'} • {message.timestamp}
          </div>

          {/* Contenu avec support Markdown */}
          <div className={`prose prose-sm max-w-none ${isBot ? 'prose-emerald' : 'prose-gray'
            }`}>
            {isBot ? (
              <ReactMarkdown
                remarkPlugins={[remarkGfm]}
                components={{
                  // Personnalisation des composants Markdown
                  h1: ({ children }) => <h1 className="pb-2 mb-3 text-xl font-bold text-emerald-800 border-b border-emerald-200">{children}</h1>,
                  h2: ({ children }) => <h2 className="mt-4 mb-2 text-lg font-bold text-emerald-700">{children}</h2>,
                  h3: ({ children }) => <h3 className="mt-3 mb-2 text-base font-bold text-emerald-600">{children}</h3>,
                  h4: ({ children }) => <h4 className="mt-3 mb-2 text-sm font-bold text-emerald-600">{children}</h4>,
                  h5: ({ children }) => <h5 className="mt-2 mb-2 text-sm font-bold text-emerald-600">{children}</h5>,
                  h6: ({ children }) => <h6 className="mt-2 mb-2 text-xs font-bold text-emerald-600">{children}</h6>,
                  p: ({ children }) => <p className="mb-2 leading-relaxed text-gray-700">{children}</p>,
                  ul: ({ children }) => <ul className="mb-3 ml-5 space-y-1 list-disc marker:text-emerald-500">{children}</ul>,
                  ol: ({ children }) => <ol className="mb-3 ml-5 space-y-2 list-decimal marker:font-bold marker:text-emerald-600">{children}</ol>,
                  li: ({ children }) => <li className="pl-1 text-gray-700">{children}</li>,
                  strong: ({ children }) => <strong className="font-bold text-emerald-800">{children}</strong>,
                  em: ({ children }) => <em className="italic text-emerald-600">{children}</em>,
                  hr: () => <hr className="my-4 border-emerald-200" />,
                  // Tableaux (syntaxe Markdown GFM) : défilement horizontal si trop larges
                  table: ({ children }) => (
                    <div className="my-4 overflow-x-auto bg-white border border-emerald-200 shadow-sm rounded-xl">
                      <table className="min-w-full text-sm border-collapse [overflow-wrap:normal]">{children}</table>
                    </div>
                  ),
                  thead: ({ children }) => (
                    <thead className="bg-gradient-to-r from-emerald-600 to-lime-600">{children}</thead>
                  ),
                  tbody: ({ children }) => (
                    <tbody className="divide-y divide-emerald-100 [&>tr:nth-child(even)]:bg-emerald-50/60">{children}</tbody>
                  ),
                  tr: ({ children }) => <tr className="transition-colors hover:bg-emerald-100/50">{children}</tr>,
                  th: ({ children }) => (
                    <th className="px-3 py-2 sm:px-4 sm:py-3 text-xs font-semibold tracking-wide text-left text-white uppercase whitespace-nowrap">
                      {children}
                    </th>
                  ),
                  td: ({ children }) => (
                    <td className="px-3 py-2 sm:px-4 sm:py-3 min-w-[8rem] leading-relaxed text-gray-700 align-top">{children}</td>
                  ),
                  blockquote: ({ children }) => (
                    <blockquote className="py-2 pl-4 mb-3 border-l-4 border-emerald-300 rounded-r-lg bg-emerald-50/50">
                      {children}
                    </blockquote>
                  ),
                  // Les blocs de code sont déjà enveloppés dans <pre> par react-markdown
                  pre: ({ children }) => (
                    <pre className="p-3 mb-3 overflow-x-auto bg-gray-100 rounded-lg">{children}</pre>
                  ),
                  code: ({ children }) => (
                    <code className="bg-emerald-100 text-emerald-800 px-1 py-0.5 rounded text-xs font-mono">
                      {children}
                    </code>
                  ),
                }}
              >
                {message.content}
              </ReactMarkdown>
            ) : (
              <div className="text-gray-700">{message.content}</div>
            )}
          </div>

          {/* Option discrète sous la réponse du bot */}
          {isBot && !message.isError && onGenerateMindmap && (
            <div className="flex justify-end pt-2 mt-3 border-t border-emerald-100/70">
              <button
                onClick={() => onGenerateMindmap(message)}
                className="flex items-center px-2 py-1 space-x-1 text-xs font-medium text-emerald-600 transition-colors rounded-lg hover:bg-emerald-100 hover:text-emerald-800"
                title="Visualiser cette réponse sous forme de mindmap"
              >
                <Brain className="w-3.5 h-3.5" />
                <span>Générer une mindmap</span>
              </button>
            </div>
          )}
        </div>
      </div>
    </div>
  );
};

// Composant principal IdeazBot
const IdeazBotInterface = () => {
  const { user, loading } = useAuth();
  const [messages, setMessages] = useState<Message[]>([]);
  const [inputText, setInputText] = useState('');
  const [isLoading, setIsLoading] = useState(false);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [currentConversationId, setCurrentConversationId] = useState<number | null>(null);

  const fetchConversations = async () => {
    try {
      const token = localStorage.getItem('authToken');
      if (!token) return;
      const res = await fetch(`${API_URL}/api/conversations`, {
        headers: { Authorization: `Bearer ${token}` }
      });
      const data = await res.json();
      if (data.success) {
        setConversations(data.conversations.map((conv: Conversation) => ({
          ...conv,
          timestamp: formatDateTime(conv.createdAt)
        })));
      }
    } catch (err) {
      console.error(err);
    }
  };

  useEffect(() => {
    if (user) {
      fetchConversations();
    }
  }, [user]);
  const [currentConversation, setCurrentConversation] = useState<Conversation | null>(null);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const [currentTime, setCurrentTime] = useState(new Date());

  useEffect(() => {
    const timer = setInterval(() => setCurrentTime(new Date()), 60000);
    return () => clearInterval(timer);
  }, []);

  const getGreeting = () => {
    const hour = currentTime.getHours();
    if (hour < 12) return "Bonjour";
    if (hour < 18) return "Bon après-midi";
    return "Bonsoir";
  };

  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  };

  useEffect(() => {
    scrollToBottom();
  }, [messages]);

  const handleSendMessage = async () => {
    const messageCopy = inputText.trim();
    if (!messageCopy || isLoading) return;

    const userMessage: Message = {
      id: Date.now(),
      content: messageCopy,
      isBot: false,
      timestamp: new Date().toLocaleTimeString('fr-FR', { hour: '2-digit', minute: '2-digit' })
    };

    setMessages(prev => [...prev, userMessage]);
    setInputText('');
    setIsLoading(true);

    try {
      const token = localStorage.getItem('authToken');
      if (!token) {
        redirectToLogin();
        return;
      }

      const response = await fetch(`${API_URL}/api/chat`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${token}`
        },
        body: JSON.stringify({ message: messageCopy, conversationId: currentConversationId }),
      });

      if (isAuthError(response.status)) {
        redirectToLogin();
        return;
      }

      const data = await response.json();

      if (!response.ok) {
        throw new Error(data.error || 'Erreur inattendue de l\'API');
      }
      if (!data.success) {
        throw new Error(data.error || 'La réponse a échoué');
      }

      // Format the bot response from Flask backend
      const formattedContent = data.message || 'Pas de contenu généré';

      const botResponse: Message = {
        id: Date.now() + 1,
        content: formattedContent,
        isBot: true,
        timestamp: new Date().toLocaleTimeString('fr-FR', { hour: '2-digit', minute: '2-digit' })
      };

      setMessages(prev => [...prev, botResponse]);

      if (data.conversationId && !currentConversationId) {
        setCurrentConversationId(data.conversationId);
        fetchConversations(); // refresh list to show newly created conversation
      }

    } catch (error) {
      console.error('Erreur:', error);
      // Les messages d'erreur du backend sont déjà rédigés pour l'utilisateur
      const errorMsg = error instanceof Error
        ? `${error.message}. Veuillez réessayer dans quelques instants.`
        : 'Une erreur inattendue est survenue.';

      const errorMessage: Message = {
        id: Date.now() + 1,
        content: `⚠️ Désolé, une erreur s'est produite : ${errorMsg}`,
        isBot: true,
        timestamp: new Date().toLocaleTimeString('fr-FR', { hour: '2-digit', minute: '2-digit' }),
        isError: true
      };
      setMessages(prev => [...prev, errorMessage]);
    } finally {
      setIsLoading(false);
    }
  };

  const handleKeyPress = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSendMessage();
    }
  };

  const handleNewConversation = () => {
    setMessages([]);
    setCurrentConversation(null);
    setCurrentConversationId(null);
    setInputText('');
  };

  // Ouvre la mindmap construite à partir d'une réponse précise du bot
  const handleGenerateMindmap = (message: Message) => {
    const mindmapData = {
      messages: [message],
      timestamp: new Date().toISOString()
    };
    localStorage.setItem('mindmapData', JSON.stringify(mindmapData));
    window.location.href = '/Mindmap';
  };

  const handleSelectConversation = async (conv: Conversation) => {
    setCurrentConversation(conv);
    setCurrentConversationId(conv.id);
    setMessages([]);
    setIsLoading(true);

    try {
      const token = localStorage.getItem('authToken');
      const res = await fetch(`${API_URL}/api/conversations/${conv.id}`, {
        headers: { Authorization: `Bearer ${token}` }
      });
      const data = await res.json();
      if (data.success) {
        setMessages(data.messages.map((msg: Message) => ({
          ...msg,
          timestamp: formatTime(msg.createdAt)
        })));
      }
    } catch (err) {
      console.error(err);
    } finally {
      setIsLoading(false);
    }
  };

  const handleDeleteConversation = async (id: number) => {
    try {
      const token = localStorage.getItem('authToken');
      await fetch(`${API_URL}/api/conversations/${id}`, {
        method: 'DELETE',
        headers: { Authorization: `Bearer ${token}` }
      });
      setConversations(prev => prev.filter(conv => conv.id !== id));
      if (currentConversationId === id) {
        handleNewConversation();
      }
    } catch (err) {
      console.error(err);
    }
  };

  const downloadConversation = () => {
    const conversationText = messages.map(msg =>
      `${msg.isBot ? 'IdeazBot' : 'Vous'} (${msg.timestamp}):\n${msg.content}\n\n`
    ).join('');

    const blob = new Blob([conversationText], { type: 'text/plain' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `ideazbot-conversation-${Date.now()}.txt`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center h-dvh bg-gradient-to-br from-emerald-50 via-lime-50 to-yellow-50">
        <div className="text-center">
          <div className="flex items-center justify-center w-16 h-16 mb-4 rounded-full bg-gradient-to-r from-emerald-600 to-lime-600 animate-pulse">
            <Brain className="w-8 h-8 text-white" />
          </div>
          <p className="text-gray-600">Connexion en cours...</p>
        </div>
      </div>
    );
  }

  return (
    // h-dvh : hauteur réellement visible (sur mobile, h-screen passe sous la barre d'adresse)
    <div className="relative flex flex-col overflow-hidden h-dvh bg-gradient-to-br from-emerald-50 via-lime-50 to-yellow-50">
      {/* Arrière-plan animé */}
      <AnimatedBackground />

      {/* Header avec branding IdeazBot */}
      <header className="sticky top-0 z-10 border-b shadow-sm border-emerald-200/30 bg-white/80 backdrop-blur-sm pt-[env(safe-area-inset-top)]">
        <div className="max-w-6xl px-3 py-2 mx-auto sm:px-6 sm:py-4">
          <div className="flex items-center justify-between gap-2">
            {/* Left side - Logo et branding (sur mobile : logo + salutation seulement) */}
            <div className="flex items-center min-w-0 gap-2 sm:gap-4">
              <div className="flex items-center flex-shrink-0 space-x-3">
                <div className="flex items-center justify-center shadow-lg w-9 h-9 sm:w-10 sm:h-10 bg-gradient-to-r from-emerald-600 to-lime-600 rounded-xl">
                  <Brain className="w-5 h-5 text-white sm:w-6 sm:h-6" />
                </div>
                <div className="hidden sm:block">
                  <h1 className="text-xl font-bold text-transparent bg-gradient-to-r from-emerald-600 to-lime-600 bg-clip-text">
                    IdeazBot
                  </h1>
                  <p className="text-xs font-medium text-emerald-500">Intelligence Créative</p>
                </div>
              </div>
              <div className="flex-shrink-0 hidden w-px h-8 bg-emerald-200 sm:block"></div>
              <div className="min-w-0">
                <h2 className="text-base font-semibold text-gray-800 truncate sm:text-lg">
                  {getGreeting()}, {user?.prenom || user?.nom}
                </h2>
                <p className="hidden text-sm text-emerald-600 truncate md:block">Prêt à générer des idées innovantes ?</p>
              </div>
            </div>

            {/* Right side - Actions */}
            <div className="flex items-center flex-shrink-0 gap-0.5 sm:gap-2">
              {messages.length > 0 && (
                <button
                  onClick={downloadConversation}
                  className="p-2 text-emerald-600 transition-colors hover:bg-emerald-50 rounded-xl"
                  title="Télécharger la conversation"
                >
                  <Download className="w-5 h-5" />
                </button>
              )}
              <button
                onClick={() => setHistoryOpen(true)}
                className="relative p-2 text-emerald-600 transition-colors hover:bg-emerald-50 rounded-xl"
                title="Historique des conversations"
              >
                <History className="w-5 h-5" />
                {/* Pastille qui s'élargit si le nombre a plusieurs chiffres */}
                <div className="absolute flex items-center justify-center min-w-[1.1rem] h-[1.1rem] px-1 rounded-full -top-1 -right-1 bg-gradient-to-r from-emerald-500 to-lime-500">
                  <span className="text-[10px] font-bold leading-none text-white">{conversations.length}</span>
                </div>
              </button>
              <div className="hidden w-px h-6 mx-1 bg-emerald-200 sm:block"></div>
              <button
                onClick={redirectToLogin}
                className="p-2 text-emerald-600 transition-colors hover:bg-red-50 hover:text-red-600 rounded-xl"
                title="Se déconnecter"
                aria-label="Se déconnecter"
              >
                <LogOut className="w-5 h-5" />
              </button>
            </div>
          </div>
        </div>
      </header>

      {/* Chat Area */}
      <div className="relative flex-1 min-h-0 overflow-hidden">
        <div className="flex flex-col h-full max-w-5xl mx-auto">
          {/* Messages */}
          <div className="flex-1 min-h-0 px-3 pt-4 overflow-y-auto sm:px-6 sm:pt-6">
            {messages.length === 0 ? (
              <div className="flex flex-col items-center justify-center min-h-full px-2 py-6 text-center">
                <div className="flex items-center justify-center w-16 h-16 mb-4 shadow-xl sm:w-20 sm:h-20 sm:mb-6 bg-gradient-to-r from-emerald-600 to-lime-600 rounded-3xl">
                  <Lightbulb className="w-8 h-8 text-white sm:w-10 sm:h-10" />
                </div>
                <h2 className="mb-3 text-2xl font-bold text-transparent sm:text-3xl bg-gradient-to-r from-emerald-600 to-lime-600 bg-clip-text">
                  Créons ensemble votre prochaine grande idée
                </h2>
                <p className="max-w-lg mb-6 text-base text-gray-600 sm:mb-8 sm:text-lg">
                  Décrivez votre projet, vos défis ou vos objectifs. IdeazBot vous accompagne avec des insights personnalisés et des solutions créatives.
                </p>
                <div className="flex flex-wrap items-center justify-center gap-x-6 gap-y-3">
                  <div className="flex items-center space-x-2 text-emerald-600">
                    <Sparkles className="w-5 h-5" />
                    <span className="text-sm font-medium">Génération d'idées</span>
                  </div>
                  <div className="flex items-center space-x-2 text-lime-600">
                    <Rocket className="w-5 h-5" />
                    <span className="text-sm font-medium">Innovation guidée</span>
                  </div>
                  <div className="flex items-center space-x-2 text-lime-600">
                    <Zap className="w-5 h-5" />
                    <span className="text-sm font-medium">Solutions rapides</span>
                  </div>
                </div>
              </div>
            ) : (
              <>
                {messages.map((message) => (
                  <ChatMessage
                    key={message.id}
                    message={message}
                    isBot={message.isBot}
                    onGenerateMindmap={handleGenerateMindmap}
                  />
                ))}
                {isLoading && (
                  <div className="flex justify-start mb-4 sm:mb-6">
                    <div className="flex items-start gap-2 sm:gap-3">
                      <div className="flex items-center justify-center w-8 h-8 rounded-full shadow-lg sm:w-10 sm:h-10 bg-gradient-to-r from-emerald-600 to-lime-600">
                        <Brain className="w-4 h-4 text-white sm:w-5 sm:h-5" />
                      </div>
                      <div className="p-3 border sm:p-4 bg-gradient-to-br from-emerald-50 to-lime-50 rounded-2xl border-emerald-100/50">
                        <div className="flex space-x-1">
                          {[...Array(3)].map((_, i) => (
                            <div
                              key={i}
                              className="w-2 h-2 rounded-full bg-gradient-to-r from-emerald-500 to-lime-500 animate-bounce"
                              style={{ animationDelay: `${i * 0.2}s` }}
                            />
                          ))}
                        </div>
                        <p className="mt-2 text-xs text-emerald-600">IdeazBot réfléchit...</p>
                      </div>
                    </div>
                  </div>
                )}
              </>
            )}
            <div ref={messagesEndRef} />
          </div>

          {/* Input area avec design IdeazBot (marge du bas : barre d'accueil des iPhone) */}
          <div className="px-3 pt-3 sm:px-6 sm:pt-6 pb-[max(0.75rem,env(safe-area-inset-bottom))] sm:pb-6 border-t border-emerald-200/30 bg-white/50 backdrop-blur-sm">
              <>
                <div className="flex items-end gap-2 sm:gap-3">
                  <div className="relative flex-1 min-w-0">
                    {/* text-base (16px) : évite le zoom automatique d'iOS sur les champs de saisie */}
                    <textarea
                      value={inputText}
                      onChange={(e) => setInputText(e.target.value)}
                      onKeyPress={handleKeyPress}
                      maxLength={MAX_MESSAGE_LENGTH}
                      placeholder="Décrivez votre idée, votre projet ou posez votre question..."
                      className="w-full p-3 pb-6 text-base text-gray-900 placeholder-gray-500 transition-all duration-200 border-2 outline-none resize-none sm:p-4 sm:pb-6 sm:min-h-[6.5rem] border-emerald-200/50 rounded-2xl focus:border-emerald-500 focus:ring-2 focus:ring-emerald-500/20 bg-white/80 backdrop-blur-sm"
                      rows={2}
                    />
                    <div className={`absolute text-xs pointer-events-none bottom-3 right-3 ${inputText.length >= MAX_MESSAGE_LENGTH ? 'text-red-500' : 'text-emerald-400'}`}>
                      {inputText.length}/{MAX_MESSAGE_LENGTH}
                    </div>
                  </div>

                  {/* Actions toolbar */}
                  <div className="flex items-center gap-1 pb-2 sm:gap-2">
                    <button
                      onClick={handleSendMessage}
                      disabled={!inputText.trim() || isLoading}
                      aria-label="Envoyer"
                      className="p-3 text-white transition-all duration-200 transform shadow-lg sm:p-4 bg-gradient-to-r from-emerald-600 to-lime-600 hover:from-emerald-700 hover:to-lime-700 disabled:from-gray-300 disabled:to-gray-400 rounded-2xl disabled:cursor-not-allowed hover:shadow-xl hover:scale-105 disabled:transform-none"
                    >
                      {isLoading ? (
                        <div className="flex space-x-1">
                          {[...Array(3)].map((_, i) => (
                            <div
                              key={i}
                              className="w-1 h-1 bg-white rounded-full animate-bounce"
                              style={{ animationDelay: `${i * 0.1}s` }}
                            />
                          ))}
                        </div>
                      ) : (
                        <Send className="w-5 h-5" />
                      )}
                    </button>
                  </div>
                </div>
              </>
            <div className="flex items-center justify-center gap-4 mt-2 text-xs text-emerald-500 sm:justify-between sm:mt-3">
              {/* Raccourcis clavier inutiles sur écran tactile */}
              <span className="hidden sm:inline">Appuyez sur Entrée pour envoyer, Shift+Entrée pour une nouvelle ligne</span>
              <span>Propulsé par IdeazBot AI</span>
            </div>
          </div>
        </div>
      </div>

      {/* Floating History Panel */}
      <FloatingHistoryPanel
        isOpen={historyOpen}
        onClose={() => setHistoryOpen(false)}
        conversations={conversations}
        onSelectConversation={handleSelectConversation}
        onDeleteConversation={handleDeleteConversation}
        onNewConversation={handleNewConversation}
      />

      {/* Styles CSS personnalisés pour les animations */}
      <style jsx>{`
        @keyframes float {
          0%, 100% { transform: translateY(0px) rotate(0deg); }
          25% { transform: translateY(-10px) rotate(2deg); }
          50% { transform: translateY(-5px) rotate(-1deg); }
          75% { transform: translateY(-15px) rotate(1deg); }
        }
        
        .animate-float {
          animation: float 4s ease-in-out infinite;
        }
      `}</style>
    </div>
  );
};

export default IdeazBotInterface;