export const config = {
    name: "format-market-cap",
    type: "event",
    subscribes: ["format-market-cap"],
    flows: ["market-cap-inference-flow"]
};

export const handler = async (event: any, { state, logger }: any) => {
    try {
        const { jobId, result } = event;
        logger.info(`[Step4:Format] Saving market cap result for job ${jobId}`);

        // Retrieve original job
        const job = await state.get('market_cap_jobs', jobId);

        if (!job) {
            logger.error(`[Step4:Format] Job ${jobId} not found in state.`);
            return;
        }

        // Update state with result
        await state.set('market_cap_jobs', jobId, {
            ...job,
            status: 'completed',
            result,
            completedAt: new Date().toISOString()
        });

        logger.info(`[Step4:Format] Job ${jobId} completed successfully.`);

    } catch (error: any) {
        logger.error(`[Step4:Format] Error: ${error.message}`);
        throw error;
    }
};
