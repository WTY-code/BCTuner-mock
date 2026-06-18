'use strict';

const { WorkloadModuleBase } = require('@hyperledger/caliper-core');
const fs = require('fs');
const path = require('path');

class CustomWorkload extends WorkloadModuleBase {
    constructor() {
        super();
        this.transactions = [];
        this.currentIndex = 0;
        this.contractId = 'fabcar'; // 默认合约ID
    }

    async initializeWorkloadModule(workerIndex, totalWorkers, roundIndex, roundArguments, sutAdapter, sutContext) {
        await super.initializeWorkloadModule(workerIndex, totalWorkers, roundIndex, roundArguments, sutAdapter, sutContext);
        
        try {
            // 从参数中获取交易文件路径
            const txFilePath = roundArguments.txFilePath || 'transactions.json';
            
            // 读取并解析交易数据
            const rawData = fs.readFileSync(txFilePath);
            this.transactions = JSON.parse(rawData);
            
            console.log(`Worker ${workerIndex} loaded ${this.transactions.length} transactions for round ${roundIndex}`);
            this.currentIndex = 0; // 重置索引
            
            // 从参数中获取合约ID
            if (roundArguments && roundArguments.contractId) {
                this.contractId = roundArguments.contractId;
            }
        } catch (error) {
            console.error(`Error loading transactions: ${error.message}`);
            throw error;
        }
    }

    async submitTransaction() {
        // 检查是否还有交易要发送
        if (this.currentIndex >= this.transactions.length) {
            // 返回一个空对象表示没有更多交易
            return {};
        }
        
        // 获取当前交易
        const tx = this.transactions[this.currentIndex];
        this.currentIndex++;
        
        // 构造交易请求
        const request = {
            contractId: this.contractId, // 使用实例变量
            contractVersion: 'v1',
            contractFunction: tx.functionName,
            contractArguments: tx.arguments,
            readOnly: tx.readOnly || false,
            metadata: {
                txIndex: this.currentIndex,
                workerId: this.workerIndex,
                roundId: this.roundIndex
            }
        };
        
        console.log(`Submitting transaction: ${JSON.stringify(request)}`);
        
        // 实际发送交易请求
        await this.sutAdapter.sendRequests(request);
    }

    async cleanupWorkloadModule() {
        console.log(`Worker ${this.workerIndex} completed ${this.currentIndex} transactions in round ${this.roundIndex}`);
        // 重置状态（可选）
        this.transactions = [];
        this.currentIndex = 0;
    }
}

function createWorkloadModule() {
    return new CustomWorkload();
}

module.exports.createWorkloadModule = createWorkloadModule;