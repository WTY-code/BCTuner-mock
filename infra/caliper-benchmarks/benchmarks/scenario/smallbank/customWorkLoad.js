'use strict';

const { WorkloadModuleBase } = require('@hyperledger/caliper-core');
const fs = require('fs');
const path = require('path');

const sleep = (ms) => new Promise(resolve => setTimeout(resolve, ms));

class SmallbankCustomWorkload extends WorkloadModuleBase {
    constructor() {
        super();
        this.transactions = [];
        this.currentIndex = 0;
        this.contractId = 'smallbank';
        this.contractVersion = '1.0';
    }

    async initializeWorkloadModule(workerIndex, totalWorkers, roundIndex, roundArguments, sutAdapter, sutContext) {
        await super.initializeWorkloadModule(workerIndex, totalWorkers, roundIndex, roundArguments, sutAdapter, sutContext);
        
        // 解析参数
        const txFilePath = roundArguments.txFilePath || 'transactions.json';
        if (roundArguments && roundArguments.contractId) {
            this.contractId = roundArguments.contractId;
        }
        if (roundArguments && roundArguments.contractVersion) {
            this.contractVersion = roundArguments.contractVersion;
        }
        
        // 读取并解析交易文件
        const absolutePath = path.isAbsolute(txFilePath) ? txFilePath : path.join(process.cwd(), txFilePath);
        const raw = fs.readFileSync(absolutePath);
        const allTransactions = JSON.parse(raw);
        
        // 多 Worker 分片逻辑
        // 采用 Round-Robin 方式分配
        if (totalWorkers > 1) {
            this.transactions = allTransactions.filter((_, index) => index % totalWorkers === workerIndex);
        } else {
            this.transactions = allTransactions;
        }

        this.currentIndex = 0;
        console.log(`Worker ${workerIndex}/${totalWorkers} loaded ${this.transactions.length}/${allTransactions.length} txs.`);
    }

    async submitTransaction() {
        if (this.currentIndex >= this.transactions.length) {
            return {};
        }
        
        const tx = this.transactions[this.currentIndex++];

        const request = {
            contractId: this.contractId,
            contractVersion: this.contractVersion,
            contractFunction: tx.functionName,
            contractArguments: tx.arguments || [],
            readOnly: tx.readOnly || false,
            timeout: tx.timeout || 30000,
            metadata: {
                txIdInFile: tx.id || `idx_${this.currentIndex - 1}`,
                workerId: this.workerIndex,
                roundId: this.roundIndex
            }
        };

        try {
            await this.sutAdapter.sendRequests(request);
        } catch (error) {
            if (error.message && error.message.includes('Channel has been shut down')) {
                 // 忽略 Channel 关闭错误
            } else {
                 console.error(`[Worker ${this.workerIndex}] Error submitting tx ${request.metadata.txIdInFile}: ${error}`);
            }
        }
    }

    async cleanupWorkloadModule() {
        console.log(`Worker ${this.workerIndex} sent ${this.currentIndex}/${this.transactions.length} txs in round ${this.roundIndex}`);
        this.transactions = [];
        this.currentIndex = 0;
    }
}

function createWorkloadModule() {
    return new SmallbankCustomWorkload();
}

module.exports.createWorkloadModule = createWorkloadModule;
