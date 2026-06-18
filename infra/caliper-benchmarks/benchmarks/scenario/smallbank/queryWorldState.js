'use strict';

const { WorkloadModuleBase } = require('@hyperledger/caliper-core');
const fs = require('fs');
const path = require('path');

/**
 * Workload module for querying and saving the entire Smallbank world state
 */
class QuerySmallbankWorldStateWorkload extends WorkloadModuleBase {
    /**
     * Initializes the workload module instance.
     */
    constructor() {
        super();
        this.executed = false;
        this.outputPath = '';
    }

    /**
     * Initialize the workload module with the given parameters.
     */
    async initializeWorkloadModule(workerIndex, totalWorkers, roundIndex, roundArguments, sutAdapter, sutContext) {
        await super.initializeWorkloadModule(workerIndex, totalWorkers, roundIndex, roundArguments, sutAdapter, sutContext);
        
        this.outputPath = roundArguments.outputPath || path.join(__dirname, `world_state_round${roundIndex}_worker${workerIndex}.json`);
        console.log(`Smallbank world state will be saved to: ${this.outputPath}`);
    }

    /**
     * Assemble TXs for the round.
     * @return {Promise<TxStatus[]>}
     */
    async submitTransaction() {
        if (this.executed) {
            return Promise.resolve();
        }
        this.executed = true;

        console.log('Starting Smallbank world state query...');
        const startTime = Date.now();

        try {
            // 使用Caliper的SUT适配器发送查询请求
            let request = {
                contractId: this.roundArguments.contractId || 'smallbank',
                contractVersion: '1.0',
                contractFunction: 'query_all_accounts',
                contractArguments: [],
                readOnly: true,
                timeout: 30000
            };

            console.log('Sending query_all_accounts request...');
            
            // 发送请求并等待响应
            const results = await this.sutAdapter.sendRequests(request);
            
            console.log('Received response, processing...');
            
            // 检查结果是否存在
            if (!results) {
                console.error('No results received from query');
                return Promise.resolve();
            }
            
            // 直接访问TxStatus对象的status属性
            if (!results.status) {
                console.error('No status in results');
                return Promise.resolve();
            }
            
            // 检查交易状态
            if (results.status.status !== 'success') {
                console.error('Query transaction failed with status:', results.status.status);
                console.error('Error messages:', results.status.error_messages);
                return Promise.resolve();
            }
            
            console.log('Transaction committed successfully');
            
            // 获取返回的数据 (Uint8Array)
            const worldStateUint8Array = results.status.result;
            if (!worldStateUint8Array) {
                console.error('World state result is null or undefined');
                return Promise.resolve();
            }
            
            console.log('Raw result length:', worldStateUint8Array.length);
            
            // 将Uint8Array转换为字符串
            let worldStateStr;
            try {
                // 将Uint8Array转换为Buffer，然后转换为字符串
                const worldStateBuffer = Buffer.from(worldStateUint8Array);
                worldStateStr = worldStateBuffer.toString('utf8');
                console.log('Result as string (first 500 chars):', worldStateStr.substring(0, 500) + '...');
            } catch (e) {
                console.error('Error converting Uint8Array to string:', e);
                return Promise.resolve();
            }
            
            // 解析JSON
            let parsedWorldState;
            try {
                parsedWorldState = JSON.parse(worldStateStr);
                console.log('Parsed result type:', typeof parsedWorldState);
            } catch (e) {
                console.error('Error parsing JSON:', e);
                console.error('Raw string that failed to parse (first 500 chars):', worldStateStr.substring(0, 500) + '...');
                return Promise.resolve();
            }
            
            // 检查解析后的数据
            if (!parsedWorldState) {
                console.error('Parsed world state is null or undefined');
                return Promise.resolve();
            }
            
            // 将结果保存到文件
            try {
                const jsonData = JSON.stringify(parsedWorldState, null, 2);
                fs.writeFileSync(this.outputPath, jsonData);
                
                const duration = (Date.now() - startTime) / 1000;
                
                // 安全地获取记录数量
                let recordCount = 'unknown';
                if (Array.isArray(parsedWorldState)) {
                    recordCount = parsedWorldState.length;
                } else if (typeof parsedWorldState === 'object' && parsedWorldState !== null) {
                    recordCount = Object.keys(parsedWorldState).length;
                }
                
                console.log(`Smallbank world state query completed in ${duration.toFixed(2)} seconds`);
                console.log(`Saved ${recordCount} account records to ${this.outputPath}`);
                
                // 打印一些样本数据以供验证
                if (Array.isArray(parsedWorldState) && parsedWorldState.length > 0) {
                    console.log('Sample account record:', JSON.stringify(parsedWorldState[0], null, 2));
                    console.log('Account fields available:', Object.keys(parsedWorldState[0]));
                }
            } catch (e) {
                console.error('Error writing to file:', e);
            }
        } catch (error) {
            console.error(`Error during Smallbank world state query: ${error}`);
            if (error.stack) {
                console.error(error.stack);
            }
        }

        return Promise.resolve();
    }
}

/**
 * Create a new instance of the workload module.
 * @return {WorkloadModuleInterface}
 */
function createWorkloadModule() {
    return new QuerySmallbankWorldStateWorkload();
}

module.exports.createWorkloadModule = createWorkloadModule;
