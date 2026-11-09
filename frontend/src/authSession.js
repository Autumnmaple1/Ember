let current = { required: false, authenticated: false, csrf: null };
export function authSession() { return current; }
export function setAuthSession(value) { current = value; }
export function authenticationLost() {
  if (current.required && typeof window !== 'undefined') window.dispatchEvent(new Event('ember-auth-lost'));
}
