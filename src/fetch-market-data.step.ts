
import { v4 as uuidv4 } from 'uuid';
import { spawn } from 'child_process';
import fs from 'fs/promises';
import path from 'path';

export const config = {
    name: "fetch-market-data",
    type: "event",
    subscribes: ["fetch-market-data"],
    emits: ["format-market-cap"],
    flows: ["market-cap-inference-flow"]
};

// US Country ID and PageSize defaults from user code
const COUNTRY_CONFIG = {
    name: "america",
    kr: "미국",
    countryId: 5,
    pageSize: 0
};

export const handler = async (event: any, { emit, logger }: any) => {
    let inputFile = '';
    try {
        const { jobId, ticker } = event;
        logger.info(`[Fetch] Starting market data fetch for job ${jobId}, target: ${ticker}`);

        const rawData = await crawling("us");

        logger.info(`[Fetch] Fetched ${rawData.length} items.`);

        // Check if target exists in data
        const targetItem = rawData.find((item: any) => {
            // loose match for ticker
            if (!item.name) return false;
            // item.name might be "AAPL" or "NASDAQ:AAPL"
            return item.name === ticker || item.name.endsWith(':' + ticker);
        });

        if (!targetItem) {
            throw new Error(`Target ticker ${ticker} not found in fetched data.`);
        }

        // Optimize payload size: Use top 3000 items for better training accuracy
        // Since we utilize file system for IPC, we can handle larger payloads.
        let optimizedData = rawData.slice(0, 3000);
        if (!optimizedData.find((item: any) => item.name === targetItem.name)) {
            optimizedData.push(targetItem);
        }

        logger.info(`[Fetch] Prepared ${optimizedData.length} items for analysis.`);

        // --- Execute Python Logic (Merged) ---
        const tempDir = path.resolve('temp');
        await fs.mkdir(tempDir, { recursive: true });
        inputFile = path.join(tempDir, `input_${jobId}.json`);

        const pythonInput = {
            jobId,
            ticker,
            rawData: optimizedData
        };

        await fs.writeFile(inputFile, JSON.stringify(pythonInput));

        const pythonScript = path.resolve('scripts/run_market_cap.py');
        const pythonCmd = process.env.PYTHON_MODULES_PATH
            ? path.join(process.env.PYTHON_MODULES_PATH, 'bin', 'python')
            : 'python';

        logger.info(`[Fetch:PY] Executing: ${pythonCmd} ${pythonScript}`);

        const result = await runPythonScript(pythonCmd, [pythonScript, inputFile], logger);

        logger.info(`[Fetch:PY] Success! Result: ${JSON.stringify(result)}`);

        if (result.error) {
            throw new Error(result.error);
        }

        // Emit directly to format step
        await emit({
            topic: 'format-market-cap',
            data: {
                jobId,
                result // { symbol, actual_market_cap, inferred_market_cap, ... }
            }
        });

    } catch (error: any) {
        logger.error(`[Fetch] Error: ${error.message}`);
        // Motia doesn't support emit error to next step easily, 
        // usually we update state or emit a failure event if needed.
        // Here we just log, format-market-cap won't be triggered.
    } finally {
        if (inputFile) {
            try { await fs.unlink(inputFile); } catch { }
        }
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
            if (!str.trim().startsWith('{')) {
                logger.info(`[PY] ${str.trim()}`);
            }
        });

        process.stderr.on('data', (data) => {
            const str = data.toString();
            stderrData += str;
            logger.error(`[PY-ERR] ${str.trim()}`);
        });

        process.on('close', (code) => {
            if (code !== 0) {
                reject(new Error(`Python script exited with code ${code}. Stderr: ${stderrData}`));
                return;
            }
            try {
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

const crawling = async (countryCode: string) => {
    // Columns requested by user
    const columns = [
        "name", "description", "logoid", "market_cap_basic", "sector", // Basic info & Target

        // Data fields requested by user
        "gross_margin_ttm", "operating_margin_ttm", "pre_tax_margin_ttm", "net_margin_ttm", "free_cash_flow_margin_ttm",
        "return_on_assets_fq", "return_on_equity_fq", "return_on_invested_capital_fq",
        "research_and_dev_ratio_ttm", "sell_gen_admin_exp_other_ratio_ttm",

        "total_revenue", // 'sooeip'? (Income/Revenue)
        "total_revenue_yoy_growth_ttm",
        "earnings_per_share_diluted_ttm", "earnings_per_share_diluted_yoy_growth_ttm",

        "total_assets_fq", "total_current_assets_fq", "cash_n_short_term_invest_fq",
        "total_liabilities_fq", "total_debt_fq", "net_debt_fq", "total_equity_fq",

        "current_ratio_fq", "quick_ratio_fq",
        "debt_to_equity_fq", "cash_n_short_term_invest_to_total_debt_fq", // Cash/Debt

        "cash_f_operating_activities_ttm", "cash_f_investing_activities_ttm", "cash_f_financing_activities_ttm",
        "free_cash_flow_ttm", "capital_expenditures_ttm"
    ];

    try {
        const response = await fetch(
            `https://scanner.tradingview.com/${COUNTRY_CONFIG.name}/scan`,
            {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    columns: columns,
                    ignore_unknown_fields: false,
                    options: { lang: "en" }, // Request English for consistency? Or ko? User used ko.
                    range: [0, 9999], // Fetch all
                    sort: { sortBy: "market_cap_basic", sortOrder: "desc" },
                    markets: ["america"],
                    filter: [
                        { left: "exchange", operation: "in_range", right: ["NASDAQ", "NYSE"] },
                        { left: "type", operation: "equal", right: "stock" },
                        { left: "typespecs", operation: "has", right: ["common"] } // Common stocks only
                    ],
                }),
            }
        );

        if (!response.ok) {
            throw new Error(`TradingView Scan Error: ${response.status}`);
        }

        const json = await response.json();

        // Map list to objects
        return json.data.map((item: any) => {
            const obj: any = {};
            columns.forEach((col, i) => {
                obj[col] = item.d[i];
            });
            // Convert to snake_case and numbers
            return normalizeData(obj);
        });

    } catch (error) {
        console.error("Crawling failed", error);
        throw error;
    }
};

function normalizeData(obj: any) {
    const newObj: any = {};
    Object.keys(obj).forEach((key) => {
        // Simple snake case (already mostly snake case in request, but clean up dots)
        let newKey = key.replace(/\./g, '_').toLowerCase();
        newObj[newKey] = obj[key];
    });
    return newObj;
}
