// Generated from canonical responses; do not edit.
const routes=[{method:"GET",path:new RegExp("^/api/v3/feeds/[^/]+/avatar/[^/]+$")},{method:"GET",path:new RegExp("^/api/v3/materials/[^/]+/images/[^/]+$")},{method:"GET",path:new RegExp("^/api/v3/materials/[^/]+/media/[^/]+$")}];
export const isBinaryOperation=(path:string,method:string)=>routes.some(route=>route.method===method&&route.path.test(path));
