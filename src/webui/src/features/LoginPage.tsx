import { useState, useEffect, useRef } from 'react'
import { useNavigate } from 'react-router-dom'
import { useAuthStore } from '@/stores/state'
import { useSettingsStore } from '@/stores/settings'
import { loginToServer, getAuthStatus } from '@/api/lightrag'
import { toast } from 'sonner'
import { useTranslation } from 'react-i18next'
import { Card, CardContent, CardHeader } from '@/components/ui/Card'
import Input from '@/components/ui/Input'
import Button from '@/components/ui/Button'
import { AtomIcon, DatabaseIcon, NetworkIcon } from 'lucide-react'
import AppSettings from '@/components/AppSettings'
import MagiCoreMark from '@/components/icons/MagiCoreMark'

const LoginPage = () => {
  const navigate = useNavigate()
  const { login, isAuthenticated } = useAuthStore()
  const { t } = useTranslation()
  const [loading, setLoading] = useState(false)
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [checkingAuth, setCheckingAuth] = useState(true)
  const authCheckRef = useRef(false) // Prevent duplicate calls in Vite dev mode

  useEffect(() => {
    console.log('LoginPage mounted')
  }, [])

  // Check if authentication is configured, skip login if not
  useEffect(() => {
    const checkAuthConfig = async () => {
      // Prevent duplicate calls in Vite dev mode
      if (authCheckRef.current) {
        return
      }
      authCheckRef.current = true

      try {
        // If already authenticated, redirect to home
        if (isAuthenticated) {
          navigate('/')
          return
        }

        // Check auth status
        const status = await getAuthStatus()

        // Set session flag for version check to avoid duplicate checks in App component
        if (status.core_version || status.api_version) {
          sessionStorage.setItem('VERSION_CHECKED_FROM_LOGIN', 'true')
        }

        if (!status.auth_configured && status.access_token) {
          // If auth is not configured, use the guest token and redirect
          login(
            status.access_token,
            true,
            status.core_version,
            status.api_version,
            status.webui_title || null,
            status.webui_description || null
          )
          if (status.message) {
            toast.info(status.message)
          }
          navigate('/')
          return
        }

        // Only set checkingAuth to false if we need to show the login page
        setCheckingAuth(false)
      } catch (error) {
        console.error('Failed to check auth configuration:', error)
        // Also set checkingAuth to false in case of error
        setCheckingAuth(false)
      }
      // Removed finally block as we're setting checkingAuth earlier
    }

    // Execute immediately
    checkAuthConfig()

    // Cleanup function to prevent state updates after unmount
    return () => {}
  }, [isAuthenticated, login, navigate])

  // Don't render anything while checking auth
  if (checkingAuth) {
    return null
  }

  const handleSubmit = async (e: React.FormEvent<HTMLFormElement>) => {
    e.preventDefault()
    if (!username || !password) {
      toast.error(t('login.errorEmptyFields'))
      return
    }

    try {
      setLoading(true)
      const response = await loginToServer(username, password)

      // Get previous username from localStorage
      const previousUsername = localStorage.getItem('LIGHTRAG-PREVIOUS-USER')

      // Check if it's the same user logging in again
      const isSameUser = previousUsername === username

      // If it's not the same user, clear chat history
      if (isSameUser) {
        console.log('Same user logging in, preserving chat history')
      } else {
        console.log('Different user logging in, clearing chat history')
        // Directly clear chat history instead of setting a flag
        useSettingsStore.getState().setRetrievalHistory([])
      }

      // Update previous username
      localStorage.setItem('LIGHTRAG-PREVIOUS-USER', username)

      // Check authentication mode
      const isGuestMode = response.auth_mode === 'disabled'
      login(
        response.access_token,
        isGuestMode,
        response.core_version,
        response.api_version,
        response.webui_title || null,
        response.webui_description || null
      )

      // Set session flag for version check
      if (response.core_version || response.api_version) {
        sessionStorage.setItem('VERSION_CHECKED_FROM_LOGIN', 'true')
      }

      if (isGuestMode) {
        // Show authentication disabled notification
        toast.info(
          response.message ||
            t('login.authDisabled', 'Authentication is disabled. Using guest access.')
        )
      } else {
        toast.success(t('login.successMessage'))
      }

      // Navigate to home page after successful login
      navigate('/')
    } catch (error) {
      console.error('Login failed...', error)
      toast.error(t('login.errorInvalidCredentials'))

      // Clear any existing auth state
      useAuthStore.getState().logout()
      // Clear local storage
      localStorage.removeItem('LIGHTRAG-API-TOKEN')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="magi-login-shell relative flex h-screen w-screen items-center justify-center overflow-hidden p-5">
      <div className="absolute top-4 right-4 z-10 flex items-center gap-2">
        <AppSettings className="border-border bg-card/60 border backdrop-blur-xl" />
      </div>
      <Card className="magi-login-card grid w-full max-w-[900px] overflow-hidden shadow-none md:grid-cols-[1.05fr_0.95fr]">
        <section className="magi-login-story relative hidden min-h-[520px] flex-col justify-between border-r p-10 md:flex">
          <div>
            <div className="flex items-center gap-3">
              <span className="magi-brand-mark flex size-9 items-center justify-center rounded-xl">
                <MagiCoreMark className="size-6" />
              </span>
              <span className="text-sm font-medium">MAGI Memo</span>
            </div>
            <h1 className="mt-16 max-w-sm text-[40px] leading-[1.05] font-medium tracking-[-0.045em]">
              {t('login.heroTitle')}
            </h1>
            <p className="text-muted-foreground mt-5 max-w-sm text-sm leading-6">
              {t('login.heroDescription')}
            </p>
          </div>
          <div className="grid grid-cols-3 gap-2">
            {[
              [DatabaseIcon, t('login.featureEpisodes')],
              [AtomIcon, t('login.featureAtoms')],
              [NetworkIcon, t('login.featureGraph')]
            ].map(([Icon, label]) => {
              const FeatureIcon = Icon as typeof DatabaseIcon
              return (
                <div key={label as string} className="magi-login-feature rounded-lg border p-3">
                  <FeatureIcon className="text-muted-foreground mb-4 size-4" />
                  <span className="text-xs font-medium">{label as string}</span>
                </div>
              )
            })}
          </div>
        </section>

        <section className="bg-card/50 flex min-h-[520px] flex-col justify-center px-7 py-12 sm:px-12">
          <CardHeader className="space-y-0 p-0 pb-8">
            <div className="mb-7 flex items-center gap-2.5 md:hidden">
              <span className="magi-brand-mark flex size-8 items-center justify-center rounded-lg">
                <MagiCoreMark className="size-5" />
              </span>
              <span className="text-sm font-medium">MAGI Memo</span>
            </div>
            <p className="text-muted-foreground mb-2 text-xs font-medium tracking-[0.08em] uppercase">
              {t('login.workspaceAccess')}
            </p>
            <h2 className="text-2xl font-medium tracking-[-0.035em]">
              {t('login.welcomeBack')}
            </h2>
            <p className="text-muted-foreground mt-2 text-sm leading-6">{t('login.description')}</p>
          </CardHeader>
          <CardContent className="p-0">
            <form onSubmit={handleSubmit} className="space-y-6">
              <div className="space-y-2">
                <label
                  htmlFor="username-input"
                  className="text-muted-foreground text-xs font-medium"
                >
                  {t('login.username')}
                </label>
                <Input
                  id="username-input"
                  placeholder={t('login.usernamePlaceholder')}
                  value={username}
                  onChange={(e) => setUsername(e.target.value)}
                  required
                  className="h-10 w-full"
                />
              </div>
              <div className="space-y-2">
                <label
                  htmlFor="password-input"
                  className="text-muted-foreground text-xs font-medium"
                >
                  {t('login.password')}
                </label>
                <Input
                  id="password-input"
                  type="password"
                  placeholder={t('login.passwordPlaceholder')}
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  required
                  className="h-10 w-full"
                />
              </div>
              <Button
                type="submit"
                className="mt-2 h-10 w-full text-sm font-medium"
                disabled={loading}
              >
                {loading ? t('login.loggingIn') : t('login.loginButton')}
              </Button>
            </form>
          </CardContent>
          <p className="text-muted-foreground mt-8 text-center text-[11px]">
            {t('login.secureAccess')}
          </p>
        </section>
      </Card>
    </div>
  )
}

export default LoginPage
