import { callDeepSeek } from '../deepseek';
import { runCommandInline } from './run';

interface AskOptions {
  dry: boolean;
}

export async function askCommand(text: string, options: AskOptions): Promise<void> {
  // Call DeepSeek API
  const result = await callDeepSeek(text);

  // Show explanation
  console.log(JSON.stringify({ type: 'plan', explanation: result.explanation }));

  if (!result.pseudocode.trim()) {
    console.error(JSON.stringify({ type: 'error', message: 'DeepSeek 未生成伪代码' }));
    process.exit(1);
  }

  // Show generated pseudocode
  console.log(JSON.stringify({ type: 'pseudocode', code: result.pseudocode }));

  if (options.dry) {
    console.log(JSON.stringify({ type: 'done', status: 'generated_only' }));
    return;
  }

  // Parse and execute
  await runCommandInline(result.pseudocode);
}
