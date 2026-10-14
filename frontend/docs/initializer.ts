type SwaggerOptions = {
  url:string; dom_id:string; deepLinking:boolean; validatorUrl:null;
  persistAuthorization:boolean; tryItOutEnabled:boolean; defaultModelsExpandDepth:number;
};
interface Window {SwaggerUIBundle(options:SwaggerOptions):unknown;}

window.addEventListener('load', () => {
  window.SwaggerUIBundle({
    url:'/openapi.json',dom_id:'#swagger-ui',deepLinking:true,
    validatorUrl:null,persistAuthorization:false,tryItOutEnabled:false,
    defaultModelsExpandDepth:0,
  });
});
