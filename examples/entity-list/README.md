# Entity List Example

This example demonstrates how to fetch and display entities from the Nekazari platform.

## Features

- Fetches entities using `NKZClient`
- Displays entities in a responsive grid
- Shows loading state
- Handles errors gracefully
- Refresh functionality

## Usage

Replace `src/App.tsx` with the content from `EntityListExample.tsx`:

```bash
cp examples/entity-list/EntityListExample.tsx src/App.tsx
```

Or import it in your own component:

```typescript
import EntityListExample from './examples/entity-list/EntityListExample';
```

## What It Shows

1. **API Integration**: How to use `NKZClient` to fetch data
2. **Error Handling**: Proper error handling and user feedback
3. **Loading States**: Loading indicators while fetching data
4. **UI Components**: Using `Card` and `Button` from UI-Kit
5. **Responsive Design**: Grid layout that adapts to screen size

## Customization

- Filter entities by type
- Add search functionality
- Add pagination
- Show entity details on click
- Add entity creation form

