'use strict';
window.addEventListener('load', () => {
  window.SwaggerUIBundle({
    url: '/openapi.json', dom_id: '#swagger-ui', deepLinking: true,
    validatorUrl: null, persistAuthorization: false, tryItOutEnabled: false,
    defaultModelsExpandDepth: 0,
  });
});
