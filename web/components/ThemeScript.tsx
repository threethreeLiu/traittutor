/**
 * ThemeScript - Initializes theme from localStorage before React hydration
 * This prevents the flash of wrong theme on page load.
 *
 * Must be a Server Component: in Next.js / React 19, <script> tags rendered
 * by Client Components are inert on the client. Rendering it from the server
 * inlines the snippet into the SSR HTML so the browser executes it before
 * hydration.
 */
export default function ThemeScript() {
  const themeScript = `
    (function() {
      try {
        const stored = localStorage.getItem('traittutor-theme');

        document.documentElement.classList.remove('dark', 'theme-snow', 'theme-teal');

        if (stored === 'dark') {
          document.documentElement.classList.add('dark');
        } else if (stored === 'snow') {
          document.documentElement.classList.add('theme-snow');
        } else if (stored === 'teal') {
          document.documentElement.classList.add('theme-teal');
        } else if (stored === 'light') {
          // already clean
        } else {
          // No stored preference (or a retired theme): Snow is the product default.
          document.documentElement.classList.add('theme-snow');
          localStorage.setItem('traittutor-theme', 'snow');
        }
      } catch (e) {
        /* localStorage may be disabled */
      }
    })();
  `;

  return (
    <script
      dangerouslySetInnerHTML={{ __html: themeScript }}
      suppressHydrationWarning
    />
  );
}
