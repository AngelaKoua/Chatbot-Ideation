export interface UserData {
  id: number;
  nom: string;
  prenom: string;
  email: string;
}

export interface Message {
  id: number;
  content: string;
  isBot: boolean;
  timestamp: string;
  createdAt?: string; // date ISO 8601 (UTC) renvoyée par le backend
  isError?: boolean;
}

export interface Conversation {
  id: number;
  title: string;
  preview?: string;
  messages: Message[];
  timestamp: string;
  createdAt?: string; // date ISO 8601 (UTC) renvoyée par le backend
}

export interface FloatingHistoryPanelProps {
  isOpen: boolean;
  onClose: () => void;
  conversations: Conversation[];
  onSelectConversation: (conversation: Conversation) => void;
  onDeleteConversation: (id: number) => void;
  onNewConversation: () => void;
}

export interface ChatMessageProps {
  message: Message;
  isBot: boolean;
  onGenerateMindmap?: (message: Message) => void;
}
