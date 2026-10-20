interface AuthResponse {
  success: boolean;
  token?: string;
  user?: {
    id: number;
    email: string;
    prenom?: string | null;
    nom?: string | null;
  };
  error?: string;
  message?: string;
}

// URL du backend Flask : NEXT_PUBLIC_API_URL en ligne, localhost en développement
export const API_URL = (process.env.NEXT_PUBLIC_API_URL || 'http://localhost:5000').replace(/\/+$/, '');

// Mêmes règles que le backend (app.py)
export const MAX_MESSAGE_LENGTH = 2000;
export const PASSWORD_MIN_LENGTH = 8;

export const authApi = {
  async register(userData: { email: string; password: string; prenom: string; nom: string }): Promise<AuthResponse> {
    try {
      const response = await fetch(`${API_URL}/api/auth/signup`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify(userData),
      });

      const data = await response.json();
      return data;
    } catch (error) {
      console.error('Error during registration:', error);
      return {
        success: false,
        error: error instanceof Error ? error.message : 'Une erreur est survenue lors de l\'inscription'
      };
    }
  },

  async login(credentials: { email: string; password: string }): Promise<AuthResponse> {
    try {
      const response = await fetch(`${API_URL}/api/auth/login`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify(credentials),
      });

      const data = await response.json();

      if (data.success && data.token) {
        // Stocker le token dans le localStorage
        localStorage.setItem('authToken', data.token);
      }

      return data;
    } catch (error) {
      console.error('Error during login:', error);
      return {
        success: false,
        error: error instanceof Error ? error.message : 'Une erreur est survenue lors de la connexion'
      };
    }
  },

  // Fonction pour se déconnecter
  logout(): void {
    localStorage.removeItem('authToken');
    localStorage.removeItem('mindmapData');
    sessionStorage.removeItem('userInfo');
  }
};

// Session absente ou expirée : retour à la connexion (replace : « retour » ne ramène pas sur la page protégée)
export const redirectToLogin = (): void => {
  authApi.logout();
  window.location.replace('/Connexion');
};

// Le backend répond 401 (token absent ou expiré) ou 422 (token invalide)
export const isAuthError = (status: number): boolean => status === 401 || status === 422;
