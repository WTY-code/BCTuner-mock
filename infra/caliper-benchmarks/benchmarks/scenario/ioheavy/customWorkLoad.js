'use strict';

const { WorkloadModuleBase } = require('@hyperledger/caliper-core');
const fs = require('fs');
const path = require('path');

class IoheavyCustomWorkload extends WorkloadModuleBase {
    constructor() {
        super();
        this.transactions = [];
        this.currentIndex = 0;
        this.contractId = 'ioheavy';
        this.contractVersion = '1.0';
    }

    async initializeWorkloadModule(workerIndex, totalWorkers, roundIndex, roundArguments, sutAdapter, sutContext) {
        await super.initializeWorkloadModule(workerIndex, totalWorkers, roundIndex, roundArguments, sutAdapter, sutContext);

        if (roundArguments && roundArguments.contractId)
            this.contractId = roundArguments.contractId;

        const txFilePath = roundArguments.txFilePath || 'transactions.json';
        const absolutePath = path.isAbsolute(txFilePath) ? txFilePath : path.join(process.cwd(), txFilePath);
        const raw = fs.readFileSync(absolutePath);
        const allTransactions = JSON.parse(raw);

        if (totalWorkers > 1)
            this.transactions = allTransactions.filter((_, i) => i % totalWorkers === workerIndex);
        else
            this.transactions = allTransactions;
        this.currentIndex = 0;
        console.log('Ioheavy worker loaded', this.transactions.length, 'txs');
    }

    async submitTransaction() {
        if (this.currentIndex >= this.transactions.length) return {};
        const tx = this.transactions[this.currentIndex++];
        await this.sutAdapter.sendRequests({
            contractId: this.contractId,
            contractFunction: tx.functionName,
            contractArguments: tx.arguments,
            readOnly: tx.functionName === 'batch_get',
        });
    }
}

function createWorkloadModule() {
    return new IoheavyCustomWorkload();
}

module.exports.createWorkloadModule = createWorkloadModule;
