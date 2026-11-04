import './post-web-unavailable.css';
export const postWebUnavailableLabel='Публикация недоступна';
export function PostWebBadge(){return <span className="post-web-badge" role="img" aria-label={postWebUnavailableLabel} title={postWebUnavailableLabel}>!</span>;}
