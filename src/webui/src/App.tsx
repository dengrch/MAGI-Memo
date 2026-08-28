import { useState, useCallback, useEffect, useRef } from 'react'
import ThemeProvider from '@/components/ThemeProvider'
import TabVisibilityProvider from '@/contexts/TabVisibilityProvider'
import ApiKeyAlert from '@/components/ApiKeyAlert'
import StatusIndicator from '@/components/status/StatusIndicator'
import { SiteInfo, webuiPrefix } from '@/lib/constants'
import { useBackendState, useAuthStore } from '@/stores/state'
import { useSettingsStore } from '@/stores/settings'
import { getAuthStatus } from '@/api/lightrag'
import SiteHeader from '@/features/SiteHeader'
import { InvalidApiKeyError, RequireApiKeError } from '@/api/lightrag'
import MagiCoreMark from '@/components/icons/MagiCoreMark'

import GraphViewer from '@/features/GraphViewer'
import MemoryWorkspace from '@/features/MemoryWorkspace'
import RetrievalView from '@/features/RetrievalView'
import BalthasarView from '@/features/BalthasarView'

import { Tabs, TabsContent } from '@/components/ui/Tabs'

function App() {
  const message = useBackendState.use.message()
  const enableHealthCheck = useSettingsStore.use.enableHealthCheck()
  const storedCurrentTab = useSettingsStore.use.currentTab()
  const currentTab = storedCurrentTab === 'api' ? 'documents' : storedCurrentTab
  const [apiKeyAlertOpen, setApiKeyAlertOpen] = useState(false)
  const [initializing, setInitializing] = useState(true) // Add initializing state
  const versionCheckRef = useRef(false); // Prevent duplicate calls in Vite dev mode
  const healthCheckInitializedRef = useRef(false); // Prevent duplicate health checks in Vite dev mode

  const handleApiKeyAlertOpenChange = useCallback((open: boolean) => {
    setApiKeyAlertOpen(open)
    if (!open) {
      useBackendState.getState().clear()
    }
  }, [])

  // Track component mount status with useRef
  const isMountedRef = useRef(true);

  // Set up mount/unmount status tracking
  useEffect(() => {
    isMountedRef.current = true;

    // Handle page reload/unload
    const handleBeforeUnload = () => {
      isMountedRef.current = false;
    };

    window.addEventListener('beforeunload', handleBeforeUnload);

    return () => {
      isMountedRef.current = false;
      window.removeEventListener('beforeunload', handleBeforeUnload);
    };
  }, []);

  // Health check - can be disabled
  useEffect(() => {
    // Health check function
    const performHealthCheck = async () => {
      try {
        // Only perform health check if component is still mounted
        if (isMountedRef.current) {
          await useBackendState.getState().check();
        }
      } catch (error) {
        console.error('Health check error:', error);
      }
    };

    // Set health check function in the store
    useBackendState.getState().setHealthCheckFunction(performHealthCheck);

    if (!enableHealthCheck || apiKeyAlertOpen) {
      useBackendState.getState().clearHealthCheckTimer();
      return;
    }

    // On first mount or when enableHealthCheck becomes true and apiKeyAlertOpen is false,
    // perform an immediate health check and start the timer
    if (!healthCheckInitializedRef.current) {
      healthCheckInitializedRef.current = true;
    }

    // Start/reset the health check timer using the store
    useBackendState.getState().resetHealthCheckTimer();

    // Component unmount cleanup
    return () => {
      useBackendState.getState().clearHealthCheckTimer();
    };
  }, [enableHealthCheck, apiKeyAlertOpen]);

  // Version check - independent and executed only once
  useEffect(() => {
    const checkVersion = async () => {
      // Prevent duplicate calls in Vite dev mode
      if (versionCheckRef.current) return;
      versionCheckRef.current = true;

      // Check if version info was already obtained in login page
      const versionCheckedFromLogin = sessionStorage.getItem('VERSION_CHECKED_FROM_LOGIN') === 'true';
      if (versionCheckedFromLogin) {
        setInitializing(false); // Skip initialization if already checked
        return;
      }

      try {
        setInitializing(true); // Start initialization

        // Get version info
        const token = localStorage.getItem('LIGHTRAG-API-TOKEN');
        const status = await getAuthStatus();

        // If auth is not configured and a new token is returned, use the new token
        if (!status.auth_configured && status.access_token) {
          useAuthStore.getState().login(
            status.access_token, // Use the new token
            true, // Guest mode
            status.core_version,
            status.api_version,
            status.webui_title || null,
            status.webui_description || null
          );
        } else if (token && (status.core_version || status.api_version || status.webui_title || status.webui_description)) {
          // Otherwise use the old token (if it exists)
          const isGuestMode = status.auth_mode === 'disabled' || useAuthStore.getState().isGuestMode;
          useAuthStore.getState().login(
            token,
            isGuestMode,
            status.core_version,
            status.api_version,
            status.webui_title || null,
            status.webui_description || null
          );
        }

        // Set flag to indicate version info has been checked
        sessionStorage.setItem('VERSION_CHECKED_FROM_LOGIN', 'true');
      } catch (error) {
        console.error('Failed to get version info:', error);
      } finally {
        // Ensure initializing is set to false even if there's an error
        setInitializing(false);
      }
    };

    // Execute version check
    checkVersion();
  }, []); // Empty dependency array ensures it only runs once on mount

  const handleTabChange = useCallback(
    (tab: string) => useSettingsStore.getState().setCurrentTab(tab as any),
    []
  )

  // React to backend message changes during render rather than via useEffect
  // (avoids cascading renders flagged by react-hooks/set-state-in-effect)
  const [previousMessage, setPreviousMessage] = useState(message)
  if (message !== previousMessage) {
    setPreviousMessage(message)
    if (message && (message.includes(InvalidApiKeyError) || message.includes(RequireApiKeError))) {
      setApiKeyAlertOpen(true)
    }
  }

  return (
    <ThemeProvider>
      <TabVisibilityProvider>
        {initializing ? (
          // Loading state while initializing with simplified header
          <div className="magi-app-shell flex h-screen w-screen max-md:flex-col">
            {/* Simplified header during initialization - matches SiteHeader structure */}
            <header className="magi-system-header flex h-full w-[240px] shrink-0 flex-col border-r p-3 max-md:h-14 max-md:w-full max-md:flex-row max-md:border-r-0 max-md:border-b">
              <div className="flex min-h-11 items-center px-2">
                <a href={webuiPrefix} className="flex items-center gap-2">
                  <span className="magi-brand-mark flex size-7 items-center justify-center rounded-lg">
                    <MagiCoreMark className="size-5" />
                  </span>
                  <span className="text-sm font-medium">{SiteInfo.name}</span>
                </a>
              </div>
            </header>

            {/* Loading indicator in content area */}
            <div className="flex min-w-0 flex-1 items-center justify-center max-md:pt-14">
              <div className="text-center">
                <div className="magi-loader mx-auto mb-3 size-6 animate-spin rounded-full border-2 border-t-transparent"></div>
                <p className="text-muted-foreground text-sm">Initializing workspace…</p>
              </div>
            </div>
          </div>
        ) : (
          // Main content after initialization
          <main className="magi-app-shell flex h-screen w-screen overflow-hidden">
            <Tabs
              defaultValue={currentTab}
              className="!m-0 flex min-w-0 grow !p-0 overflow-hidden max-md:flex-col"
              onValueChange={handleTabChange}
            >
              <SiteHeader />
              <div className="magi-workspace relative min-w-0 grow overflow-hidden">
                <TabsContent value="documents" className="absolute top-0 right-0 bottom-0 left-0 overflow-auto">
                  <MemoryWorkspace />
                </TabsContent>
                <TabsContent value="knowledge-graph" className="absolute top-0 right-0 bottom-0 left-0 overflow-hidden">
                  <GraphViewer />
                </TabsContent>
                <TabsContent value="retrieval" className="absolute top-0 right-0 bottom-0 left-0 overflow-hidden">
                  <RetrievalView />
                </TabsContent>
                <TabsContent value="balthasar" className="absolute top-0 right-0 bottom-0 left-0 overflow-hidden">
                  <BalthasarView />
                </TabsContent>
              </div>
            </Tabs>
            {enableHealthCheck && <StatusIndicator />}
            <ApiKeyAlert open={apiKeyAlertOpen} onOpenChange={handleApiKeyAlertOpenChange} />
          </main>
        )}
      </TabVisibilityProvider>
    </ThemeProvider>
  )
}

export default App
