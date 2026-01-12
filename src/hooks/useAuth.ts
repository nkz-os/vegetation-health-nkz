import { useState, useEffect, useCallback, useMemo } from 'react';

interface AuthContext {
  token?: string;
  tenantId?: string;
  isAuthenticated: boolean;
  user?: any;
  roles?: string[];
  login?: () => void;
  logout?: () => void;
  // SDK-compatible methods
  getToken: () => string | undefined;
  getTenantId: () => string | undefined;
  hasRole: (role: string) => boolean;
}

/**
 * Custom useAuth hook that reads from the Host's window.__nekazariAuthContext.
 * Provides both direct properties (for convenience) and SDK-compatible methods.
 */
export function useAuth(): AuthContext {
  const [auth, setAuth] = useState<Omit<AuthContext, 'getToken' | 'getTenantId' | 'hasRole'>>({
    isAuthenticated: false,
    roles: []
  });

  useEffect(() => {
    const checkAuth = () => {
      const hostAuth = (window as any).__nekazariAuthContext;
      
      if (hostAuth) {
        const newToken = typeof hostAuth.getToken === 'function' 
          ? hostAuth.getToken() 
          : hostAuth.token;
          
        if (newToken !== auth.token) {
           setAuth({
            isAuthenticated: !!newToken,
            token: newToken,
            tenantId: typeof hostAuth.getTenantId === 'function' 
              ? hostAuth.getTenantId() 
              : hostAuth.tenantId,
            user: hostAuth.user,
            roles: hostAuth.roles || [],
            login: hostAuth.login,
            logout: hostAuth.logout
          });
        }
      }
    };

    checkAuth();
    const interval = setInterval(checkAuth, 1000);
    return () => clearInterval(interval);
  }, [auth.token]);

  // SDK-compatible methods
  const getToken = useCallback(() => auth.token, [auth.token]);
  const getTenantId = useCallback(() => auth.tenantId, [auth.tenantId]);
  const hasRole = useCallback((role: string) => auth.roles?.includes(role) ?? false, [auth.roles]);

  return useMemo(() => ({
    ...auth,
    getToken,
    getTenantId,
    hasRole
  }), [auth, getToken, getTenantId, hasRole]);
}
