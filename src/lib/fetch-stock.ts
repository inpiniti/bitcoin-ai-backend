/**
 * Yahoo Finance 데이터 수집 모듈
 */

export interface StockData {
    symbol: string;
    prices: number[];
    lastDate: Date;
    count: number;
}

export async function fetchStockData(symbol: string, logger: any): Promise<StockData> {
    const interval = '1h';
    const range = '60d';
    const yahooUrl = `https://query1.finance.yahoo.com/v8/finance/chart/${symbol}?interval=${interval}&range=${range}`;

    logger.info(`[Fetch] Fetching data for ${symbol}...`);

    const response = await fetch(yahooUrl, {
        headers: {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }
    });

    if (!response.ok) {
        throw new Error(`Yahoo API Error: ${response.status}`);
    }

    const data: any = await response.json();
    if (!data.chart?.result?.[0]) {
        throw new Error(`No data found for ${symbol}`);
    }

    const chartResult = data.chart.result[0];
    const timestamps = chartResult.timestamp;
    const closes = chartResult.indicators.quote[0].close;

    // null 값 필터링
    const validPrices = closes.filter((p: any) => p !== null);
    const lastDate = new Date(timestamps[timestamps.length - 1] * 1000);

    logger.info(`[Fetch] Retrieved ${validPrices.length} data points.`);

    return {
        symbol,
        prices: validPrices,
        lastDate,
        count: validPrices.length
    };
}
