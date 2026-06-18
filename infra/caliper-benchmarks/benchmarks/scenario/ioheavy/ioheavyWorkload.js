'use strict';

const { WorkloadModuleBase } = require('@hyperledger/caliper-core');

class IoheavyWorkload extends WorkloadModuleBase {
    constructor() {
        super();
        this.txIndex = 0;
    }

    async initializeWorkloadModule(workerIndex, totalWorkers, roundIndex, roundArguments, sutAdapter, sutContext) {
        await super.initializeWorkloadModule(workerIndex, totalWorkers, roundIndex, roundArguments, sutAdapter, sutContext);
        this.mode = this.roundArguments.mode || 'batch_put';
        this.numPrefixes = this.roundArguments.numPrefixes || 10;
        this.batchSize = this.roundArguments.batchSize || 50;
    }

    async submitTransaction() {
        this.txIndex++;
        const prefix = 'p' + (this.txIndex % this.numPrefixes);
        const start = (this.txIndex * this.batchSize) % 10000;
        const args = [String(start), String(this.batchSize), prefix];
        const readOnly = this.mode === 'batch_get';

        await this.sutAdapter.sendRequests({
            contractId: 'ioheavy',
            contractFunction: this.mode,
            contractArguments: args,
            readOnly: readOnly,
        });
    }
}

function createWorkloadModule() {
    return new IoheavyWorkload();
}

module.exports.createWorkloadModule = createWorkloadModule;
