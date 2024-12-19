import Form from '@rjsf/bootstrap-4';
import validator from '@rjsf/validator-ajv8';
import { useEffect, useState } from 'react';
import { paramsSchema } from '~/stores/gameStore';
import { useStore } from '@nanostores/react';
import { api } from '~/lib/api';

function ParamsForm({ gameType, gameId }) {
  const [isClient, setIsClient] = useState(false);
  const [sessionId, setSessionId] = useState(null);
  const [isDark, setIsDark] = useState(false);
  const schema = useStore(paramsSchema);

  useEffect(() => {
    setIsClient(true);
    setIsDark(document.documentElement.classList.contains('dark'));

    // Add scoped Bootstrap CSS
    const styleTag = document.createElement('style');
    styleTag.textContent = `
      /* Scope Bootstrap styles to our form wrapper */
      .bootstrap-scope .form-group,
      .bootstrap-scope .form-control,
      .bootstrap-scope .btn,
      .bootstrap-scope .custom-select,
      .bootstrap-scope .input-group,
      .bootstrap-scope .alert,
      .bootstrap-scope .card {
        all: revert;
      }
      
      /* Import Bootstrap inside our scope */
      @import url('https://cdn.jsdelivr.net/npm/bootstrap@4.6.2/dist/css/bootstrap.min.css') (class=".bootstrap-scope");
    `;
    document.head.appendChild(styleTag);

    // Function to update theme
    const updateTheme = () => {
      const isDarkMode = document.documentElement.classList.contains('dark');
      setIsDark(isDarkMode);
      
      // Remove old dark theme if it exists
      const oldDarkTheme = document.head.querySelector('style[data-theme="dark"]');
      if (oldDarkTheme) {
        document.head.removeChild(oldDarkTheme);
      }

      // Add dark theme if needed
      if (isDarkMode) {
        const darkThemeStyle = document.createElement('style');
        darkThemeStyle.textContent = `
          /* Import Bootstrap Dark theme inside our scope */
          @import url('https://cdn.jsdelivr.net/npm/@forevolve/bootstrap-dark@1.0.0/dist/css/bootstrap-dark.min.css') (class=".bootstrap-scope");
        `;
        darkThemeStyle.setAttribute('data-theme', 'dark');
        document.head.appendChild(darkThemeStyle);
      }
    };

    // Initial theme setup
    updateTheme();

    // Watch for theme changes
    const observer = new MutationObserver((mutations) => {
      mutations.forEach((mutation) => {
        if (mutation.attributeName === 'class') {
          updateTheme();
        }
      });
    });

    observer.observe(document.documentElement, {
      attributes: true,
      attributeFilter: ['class']
    });

    // Listen for game session ready event
    const handleSessionReady = async (event) => {
      const sid = event.detail.sessionId;
      setSessionId(sid);

      // Fetch schema when session is ready
      const fullSchema = await api.getParamsSchema(sid);
        
      // Extract UI schema from properties
      const uiSchema = {};
      const schema = { ...fullSchema };  // Clone the schema
      
      if (schema?.properties) {
        for (const [key, prop] of Object.entries(schema.properties)) {
          // UI Schema is stored directly in _ui_schema
          if (prop._ui_schema) {
            uiSchema[key] = prop._ui_schema;
            delete prop._ui_schema;  // Clean up schema
          }
        }
      }
      
      paramsSchema.set({ schema, uiSchema });
    };

    window.addEventListener('gameSessionReady', handleSessionReady);
    window.addEventListener('gameSessionEnd', () => setSessionId(null));

    return () => {
      window.removeEventListener('gameSessionReady', handleSessionReady);
      window.removeEventListener('gameSessionEnd', () => setSessionId(null));
      observer.disconnect();
      document.head.removeChild(styleTag);
      const darkTheme = document.head.querySelector('style[data-theme="dark"]');
      if (darkTheme) {
        document.head.removeChild(darkTheme);
      }
    };
  }, []);

  if (!isClient || !schema) {
    return null;
  }

  return (
    <div className="bootstrap-scope">
      <style>
        {`
          #root__title, 
          #root__description {
            display: none;
          }
        `}
      </style>
      <Form
        schema={schema.schema}
        uiSchema={schema.uiSchema}
        validator={validator}
        disabled={!sessionId}
        onSubmit={async ({ formData }, originalEvent) => {
          originalEvent.preventDefault();
          if (!sessionId) return;

          await api.updateParams(sessionId, formData);
        }}
      />
    </div>
  );
}

export default ParamsForm; 