'use strict';

const { WorkloadModuleBase } = require('@hyperledger/caliper-core');
const fs = require('fs');
const path = require('path');

class TokenCustomWorkload extends WorkloadModuleBase {
    constructor() {
        super();
        this.transactions = [];
        this.currentIndex = 0;
        this.contractId = 'token_erc20';
        this.contractVersion = '1.0';
    }

    async initializeWorkloadModule(workerIndex, totalWorkers, roundIndex, roundArguments, sutAdapter, sutContext) {
        await super.initializeWorkloadModule(workerIndex, totalWorkers, roundIndex, roundArguments, sutAdapter, sutContext);

        if (roundArguments && roundArguments.contractId) {
            this.contractId = roundArguments.contractId;
        }
        if (roundArguments && roundArguments.contractVersion) {
            this.contractVersion = roundArguments.contractVersion;
        }

        const txFilePath = roundArguments.txFilePath || 'transactions.json';
        const absolutePath = path.isAbsolute(txFilePath) ? txFilePath : path.join(process.cwd(), txFilePath);
        const raw = fs.readFileSync(absolutePath);
        const allTransactions = JSON.parse(raw);

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
            contractFunction: tx.functionName,
            contractArguments: tx.arguments,
            readOnly: tx.functionName === 'balance_of',
        };
        await this.sutAdapter.sendRequests(request);
    }
}

function createWorkloadModule() {
    return new TokenCustomWorkload();
}

module.exports.createWorkloadModule = createWorkloadModule;
