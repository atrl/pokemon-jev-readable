/** Shared boundary for data written to the live feed. Never log HTTP headers. */
export function redact(value, secrets = []) {
  const known = secrets.filter(value => typeof value === 'string' && value.length > 0);
  function visit(item) {
    if (typeof item === 'string') {
      for (const secret of known) item = item.split(secret).join('[REDACTED]');
      return item.replace(/Bearer\s+[^\s"']+/gi, 'Bearer [REDACTED]');
    }
    if (Array.isArray(item)) return item.map(visit);
    if (item && typeof item === 'object') return Object.fromEntries(Object.entries(item).map(([key, child]) =>
      [key, /^(authorization|cookie|set-cookie|.*(?:api[_-]?key|access[_-]?token|password|secret))$/i.test(key) ? '[REDACTED]' : visit(child)]));
    return item;
  }
  return visit(value);
}
