/** One-click model tests share the administrator-only playground channel. */
export const MODEL_TEST_PROMPT = 'Ciallo';

export function modelTestRequest(model: string, realm: 'cn' | 'global', accountId?: string) {
  return {
    model,
    realm,
    stream: false,
    messages: [{role: 'user', content: MODEL_TEST_PROMPT}],
    ...(accountId ? {account_id: accountId} : {}),
  };
}

export interface ModelTestResult {
  content: string;
  reasoning: string;
  tokens: number | null;
  credit: number | null;
}

/** Do not report HTTP 200 error envelopes or empty responses as successful tests. */
export function parseModelTestResponse(raw: unknown): ModelTestResult {
  if (!raw || typeof raw !== 'object') throw new Error('models.testEmpty');
  const data = raw as {
    error?: string | {message?: unknown};
    choices?: {message?: {content?: string | {text?: unknown}[]; reasoning_content?: unknown}}[];
    usage?: {total_tokens?: unknown; credit?: unknown};
  };
  if (data.error) {
    const message = typeof data.error === 'string' ? data.error : data.error.message;
    throw new Error(typeof message === 'string' ? message : 'models.testInvalid');
  }
  const message = data.choices?.[0]?.message;
  const content = typeof message?.content === 'string'
    ? message.content
    : Array.isArray(message?.content)
      ? message.content.map((part: {text?: unknown}) => typeof part?.text === 'string' ? part.text : '').join('')
      : '';
  const reasoning = typeof message?.reasoning_content === 'string' ? message.reasoning_content : '';
  // Some catalog models are reasoning-only; their reasoning output is a valid reply.
  if (!content.trim() && !reasoning.trim()) throw new Error('models.testEmpty');
  const usage = data.usage;
  return {
    content,
    reasoning,
    tokens: typeof usage?.total_tokens === 'number' ? usage.total_tokens : null,
    credit: typeof usage?.credit === 'number' ? usage.credit : null,
  };
}
