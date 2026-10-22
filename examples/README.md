# Examples

This directory contains example implementations for common module patterns.

## Available Examples

### Entity List (`entity-list/`)

Demonstrates:
- Fetching entities from the API
- Displaying data in a list/grid
- Error handling
- Loading states

**Files**:
- `EntityListExample.tsx` - Complete component
- `README.md` - Usage instructions

## Using Examples

### Option 1: Copy to src/

```bash
# Copy example to your main component
cp examples/entity-list/EntityListExample.tsx src/App.tsx
```

### Option 2: Import as Component

```typescript
import EntityListExample from './examples/entity-list/EntityListExample';

const MyModule: React.FC = () => {
  return <EntityListExample />;
};
```

### Option 3: Use as Reference

Study the examples and adapt the patterns to your needs.

## Creating Your Own Example

1. Create a new directory: `examples/your-example/`
2. Add your component file
3. Add a `README.md` explaining what it demonstrates
4. Follow the same structure as existing examples

## More Examples Coming Soon

- Parcel visualization
- Data charts
- Form handling
- Real-time updates

