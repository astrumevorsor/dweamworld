import { atom } from 'nanostores';
import { api } from '../lib/api';

export interface GameInfo {
  id: string;
  title: string | null;
  description: string | null;
  tags: string[] | null;
  author: string | null;
  build_date: string | null;
  repo_link: string | null;
  buttons: Record<string, string> | null;
}

export type GameStore = Record<string, Record<string, GameInfo>>;

export const games = atom<GameStore>({});
export const isLoading = atom<boolean>(false);
export const loadingMessage = atom<string | null>(null);
export const loadingDetail = atom<string | null>(null);

export interface ParamsSchema {
  schema: Record<string, any>;
  uiSchema: Record<string, any>;
}

export const paramsSchema = atom<ParamsSchema | null>(null);

// Initialize store
export async function initializeStore() {
  try {
    // Set initial loading state to false before checking status
    isLoading.set(false);
    loadingMessage.set(null);
    loadingDetail.set(null);

    const status = await api.getStatus();
    console.log('Status response:', status);
    
    if (status.is_loading) {
      isLoading.set(true);
      loadingMessage.set(status.loading_message || null);
      loadingDetail.set(status.loading_detail || null);
      startPolling();
    }

    // Get initial games
    const gamesData = await api.getGameInfo();
    games.set(gamesData);
  } catch (error) {
    console.error('Error initializing store:', error);
    startPolling();
  }
}

function startPolling() {
  let pollInterval = setInterval(async () => {
    try {
      const status = await api.getStatus();
      console.log('Polling status:', status);
      isLoading.set(status.is_loading);
      loadingMessage.set(status.loading_message || null);
      loadingDetail.set(status.loading_detail || null);
      const gamesData = await api.getGameInfo();
      games.set(gamesData);

      // Stop polling when status is running
      if (!status.is_loading) {
        clearInterval(pollInterval);
      }
    } catch (error) {
      console.error('Error polling:', error);
    }
  }, 500);
}

// Listen for game session end to clear params schema
if (typeof window !== 'undefined') {
  window.addEventListener('gameSessionEnd', () => {
    paramsSchema.set(null);
  });
  
  initializeStore();
} 