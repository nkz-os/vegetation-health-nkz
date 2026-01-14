# Testing Guide for Nekazari Modules

Guide for testing your module locally before uploading to the platform.

## Local Development Setup

### 1. Start Development Server

```bash
npm run dev
```

This starts the module on `http://localhost:5003` with Vite proxy configured.

### 2. Configure API Proxy

The template includes a Vite proxy in `vite.config.ts` that forwards `/api` requests to the Nekazari platform.

**For testing with real API**:

1. Get a token from the staging/production environment:
   - Log in to the platform
   - Open browser DevTools → Application → Local Storage
   - Find `keycloak-token` or check Network tab for Authorization header

2. Update `vite.config.ts` proxy configuration:

```typescript
server: {
  proxy: {
    '/api': {
      target: 'https://nkz.artotxiki.com',
      changeOrigin: true,
      secure: true,
      configure: (proxy, _options) => {
        proxy.on('proxyReq', (proxyReq, req, _res) => {
          proxyReq.setHeader('Authorization', 'Bearer YOUR_TOKEN_HERE');
          proxyReq.setHeader('X-Tenant-ID', 'your-tenant-id');
        });
      },
    },
  },
},
```

### 3. Mock Data for Development

If you don't have access to a real environment, you can create mock data:

**Create `src/mocks/api.ts`**:

```typescript
export const mockEntities = [
  {
    id: 'urn:ngsi-ld:Sensor:001',
    type: 'Sensor',
    name: 'Temperature Sensor',
    temperature: { value: 22.5, unitCode: 'CEL' },
    location: {
      type: 'GeoProperty',
      value: {
        type: 'Point',
        coordinates: [-3.0, 40.0]
      }
    }
  }
];

export const mockParcels = [
  {
    id: 'parcel-001',
    name: 'Field A',
    area: 5000,
    location: {
      type: 'Polygon',
      coordinates: [[[-3.0, 40.0], [-3.1, 40.0], [-3.1, 40.1], [-3.0, 40.1], [-3.0, 40.0]]]
    }
  }
];
```

**Use mocks in development**:

```typescript
import { mockEntities } from './mocks/api';

const MyComponent: React.FC = () => {
  const [entities, setEntities] = useState([]);
  
  useEffect(() => {
    if (import.meta.env.DEV) {
      // Use mock data in development
      setEntities(mockEntities);
    } else {
      // Use real API in production
      const client = new NKZClient({ /* ... */ });
      client.get('/entities').then(setEntities);
    }
  }, []);
};
```

## Testing Checklist

Before uploading your module:

- [ ] Module builds without errors (`npm run build`)
- [ ] All TypeScript types are correct (`npm run typecheck`)
- [ ] Module loads correctly in development server
- [ ] API calls work (or mocks work)
- [ ] UI components render correctly
- [ ] Authentication flow works (if applicable)
- [ ] Error handling works
- [ ] Loading states work
- [ ] Responsive design works (mobile/tablet/desktop)
- [ ] `manifest.json` is valid
- [ ] Icon and assets are included
- [ ] Module exports default component

## Common Issues

### CORS Errors

**Problem**: `Access to fetch at '...' has been blocked by CORS policy`

**Solution**: Use the Vite proxy (already configured) or ensure CORS is enabled for your development domain.

### Module Not Loading

**Problem**: Module doesn't appear in the platform

**Check**:
1. `manifest.json` is valid JSON
2. `build_config.scope` matches `vite.config.ts` federation name
3. `remoteEntry.js` exists in `dist/assets/`
4. Component exports default

### Authentication Not Working

**Problem**: `useAuth()` returns undefined or null

**Solution**: Ensure you're testing within the platform context. In standalone development, you may need to mock authentication.

## Testing in Production Environment

Once your module is uploaded:

1. Check validation status via API or admin panel
2. Activate the module for your tenant
3. Test in the actual platform environment
4. Verify all features work with real data
5. Check performance and loading times

## Support

For testing issues:
- Email: nekazari@artotxiki.com
- Check [External Developer Guide](https://github.com/k8-benetis/nekazari-public/blob/main/docs/development/EXTERNAL_DEVELOPER_GUIDE.md) troubleshooting section

















