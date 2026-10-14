// Generated from canonical responses; do not edit.
const routes=[{method:"GET",path:new RegExp("^/api/materials/[^/]+/images/[^/]+$")},{method:"GET",path:new RegExp("^/api/materials/[^/]+/media/[^/]+$")},{method:"GET",path:new RegExp("^/api/materials/[^/]+/versions/[^/]+/[^/]+/[^/]+$")}];
export const isBinaryOperation=(path:string,method:string)=>routes.some(route=>route.method===method&&route.path.test(path));
