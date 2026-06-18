'use strict';

const { WorkloadModuleBase } = require('@hyperledger/caliper-core');

class TokenWorkload extends WorkloadModuleBase {
    constructor() {
        super();
        this.txIndex = 0;
    }

    async initializeWorkloadModule(workerIndex, totalWorkers, roundIndex, roundArguments, sutAdapter, sutContext) {
        await super.initializeWorkloadModule(workerIndex, totalWorkers, roundIndex, roundArguments, sutAdapter, sutContext);
        this.mode = this.roundArguments.mode || 'transfer';
        this.numAccounts = this.roundArguments.numAccounts || 100;
        this.skew = this.roundArguments.skew || 1.0;
    }

    _pickAccount() {
        // Simple Zipfian approximation: use power-law on a random uniform
        const r = Math.random();
        const a = Math.floor(Math.pow(r, 1.0 / this.skew) * this.numAccounts);
        return 'a' + a;
    }

    async submitTransaction() {
        this.txIndex++;
        let fn, args, readOnly = false;

        if (this.mode === 'mint') {
            const account = this._pickAccount();
            const amount = Math.floor(Math.random() * 10000) + 1000;
            fn = 'mint';
            args = [account, String(amount)];
        } else if (this.mode === 'balance_of') {
            fn = 'balance_of';
            args = [this._pickAccount()];
            readOnly = true;
        } else {
            // transfer mode
            const from = this._pickAccount();
            let to = this._pickAccount();
            while (to === from) to = this._pickAccount();
            const amount = Math.floor(Math.random() * 500) + 1;
            fn = 'transfer';
            args = [from, to, String(amount)];
        }

        const request = {
            contractId: 'token_erc20',
            contractFunction: fn,
            contractArguments: args,
            readOnly: readOnly,
        };
        await this.sutAdapter.sendRequests(request);
    }
}

function createWorkloadModule() {
    return new TokenWorkload();
}

module.exports.createWorkloadModule = createWorkloadModule;
