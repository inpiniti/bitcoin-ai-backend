
import { spawn } from 'child_process';
import fs from 'fs/promises';
import path from 'path';

export const config = {
    name: "analyze-market-cap",
    type: "event", // event 타입
    subscribes: ["analyze-market-cap"],
    emits: ["format-market-cap"],
    flows: ["market-cap-inference-flow"]
};

export const handler = async (event: any, { emit, logger, state }: any) => {
    const { jobId, ticker, rawData } = event;
    logger.info(`[AMC:TS] Starting analysis for ${ticker} (Job: ${jobId})`);

    // 1. Prepare IO paths
    const tempDir = path.resolve('temp');
    await fs.mkdir(tempDir, { recursive: true });
    const inputFile = path.join(tempDir, `input_${jobId}.json`);

    try {
        // 2. Save input data to file
        await fs.writeFile(inputFile, JSON.stringify(event));
        logger.info(`[AMC:TS] Input saved to ${inputFile}`);

        // 3. Run Python Script
        const pythonScript = path.resolve('scripts/run_market_cap.py');
        // Docker 환경에서는 venv python 사용
        // 로컬에서는 시스템 python 사용 (fallback)
        const pythonCmd = process.env.PYTHON_MODULES_PATH
            ? path.join(process.env.PYTHON_MODULES_PATH, 'bin', 'python')
            : 'python';

        logger.info(`[AMC:TS] Executing: ${pythonCmd} ${pythonScript}`);

        const result = await runPythonScript(pythonCmd, [pythonScript, inputFile], logger);

        logger.info(`[AMC:TS] Python execution success. Result: ${JSON.stringify(result)}`);

        if (result.error) {
            throw new Error(result.error);
        }

        // 4. Update State & Emit
        await emit({
            topic: "format-market-cap",
            data: {
                jobId,
                result
            }
        });

    } catch (error: any) {
        logger.error(`[AMC:TS] Execution failed: ${error.message}`);

        // Update State to Error
        const job = await state.get('market_cap_jobs', jobId);
        if (job) {
            job.status = 'error';
            job.error = error.message;
            await state.set('market_cap_jobs', jobId, job);
        }
        // Don't rethrow to avoid endless retry loop in queue, just log.
    } finally {
        // Cleanup
        try {
            await fs.unlink(inputFile);
        } catch { }
    }
};

const runPythonScript = (command: string, args: string[], logger: any): Promise<any> => {
    return new Promise((resolve, reject) => {
        const process = spawn(command, args);

        let stdoutData = '';
        let stderrData = '';

        process.stdout.on('data', (data) => {
            const str = data.toString();
            stdoutData += str;
            // 실시간 로그 출력 (JSON 결과 제외)
            if (!str.trim().startsWith('{')) {
                logger.info(`[AMC:PY] ${str.trim()}`);
            }
        });

        process.stderr.on('data', (data) => {
            const str = data.toString();
            stderrData += str;
            logger.error(`[AMC:PY-ERR] ${str.trim()}`);
        });

        process.on('close', (code) => {
            if (code !== 0) {
                reject(new Error(`Python script exited with code ${code}. Stderr: ${stderrData}`));
                return;
            }
            try {
                // Find JSON in stdout (last line usually)
                const lines = stdoutData.trim().split('\n');
                const lastLine = lines[lines.length - 1];
                const json = JSON.parse(lastLine);
                resolve(json);
            } catch (e: any) {
                reject(new Error(`Failed to parse Python output: ${e.message}. Stdout: ${stdoutData}`));
            }
        });
    });
};
