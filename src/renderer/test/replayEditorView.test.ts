import { describe, expect, it } from 'vitest';
import { jsonOrThrow } from '../components/ReplayEditorView';

describe('ReplayEditorView jsonOrThrow', () => {
  it('returns parsed JSON for successful responses', async () => {
    const response = new Response(JSON.stringify([{ file: 'demo.wps.json' }]), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    });

    await expect(jsonOrThrow(response, 'load scripts')).resolves.toEqual([
      { file: 'demo.wps.json' },
    ]);
  });

  it('throws backend detail for failed JSON responses', async () => {
    const response = new Response(JSON.stringify({ detail: 'replay busy' }), {
      status: 409,
      headers: { 'Content-Type': 'application/json' },
    });

    await expect(jsonOrThrow(response, 'replay script')).rejects.toThrow('replay busy');
  });

  it('throws nested provider error messages', async () => {
    const response = new Response(JSON.stringify({ error: { message: 'invalid token' } }), {
      status: 401,
      headers: { 'Content-Type': 'application/json' },
    });

    await expect(jsonOrThrow(response, 'start agent')).rejects.toThrow('invalid token');
  });

  it('serializes structured backend details', async () => {
    const response = new Response(JSON.stringify({ detail: [{ msg: 'script missing', loc: ['file'] }] }), {
      status: 404,
      headers: { 'Content-Type': 'application/json' },
    });

    await expect(jsonOrThrow(response, 'read script')).rejects.toThrow('script missing');
  });

  it('throws plain-text bodies for failed non-JSON responses', async () => {
    const response = new Response('service offline', { status: 503 });

    await expect(jsonOrThrow(response, 'load traces')).rejects.toThrow('service offline');
  });
});
