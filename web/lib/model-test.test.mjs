import assert from 'node:assert/strict';
import {test} from 'node:test';
import {MODEL_TEST_PROMPT, modelTestRequest, parseModelTestResponse} from './model-test.ts';

test('one click sends only Ciallo and explicitly preserves the selected realm/model', () => {
  assert.equal(MODEL_TEST_PROMPT, 'Ciallo');
  for (const realm of ['cn', 'global']) {
    assert.deepEqual(modelTestRequest('glm-5.2', realm), {
      model: 'glm-5.2', realm, stream: false,
      messages: [{role: 'user', content: 'Ciallo'}],
    });
  }
});

test('normal text reply exposes upstream tokens/credits, including zero', () => {
  assert.deepEqual(parseModelTestResponse({
    choices: [{message: {content: 'Ciallo～'}}], usage: {total_tokens: 0, credit: 0},
  }), {content: 'Ciallo～', reasoning: '', tokens: 0, credit: 0});
});

test('reasoning-only models and content parts are valid replies', () => {
  assert.equal(parseModelTestResponse({choices: [{message: {content: null, reasoning_content: 'Reasoning reply'}}]}).reasoning, 'Reasoning reply');
  assert.equal(parseModelTestResponse({choices: [{message: {content: [{type: 'text', text: 'Hi'}, {text: '!'}]}}]}).content, 'Hi!');
});

test('do not invent credit or token usage when upstream omits them', () => {
  const result = parseModelTestResponse({choices: [{message: {content: 'Hello'}}]});
  assert.equal(result.tokens, null);
  assert.equal(result.credit, null);
});

test('HTTP 200 error envelopes and missing/empty replies are failures', () => {
  assert.throws(() => parseModelTestResponse({error: {message: 'No healthy public accounts'}}), /No healthy public accounts/);
  assert.throws(() => parseModelTestResponse({error: 'quota exceeded'}), /quota exceeded/);
  for (const value of [null, 'oops', {}, {choices: []}, {choices: [{message: {content: '  '}}]}]) {
    assert.throws(() => parseModelTestResponse(value), /models.testEmpty/);
  }
});
