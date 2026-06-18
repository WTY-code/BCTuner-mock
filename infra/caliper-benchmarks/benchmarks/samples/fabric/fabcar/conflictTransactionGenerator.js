/*
* Licensed under the Apache License, Version 2.0 (the "License");
* you may not use this file except in compliance with the License.
* You may obtain a copy of the License at
*
* http://www.apache.org/licenses/LICENSE-2.0
*
* Unless required by applicable law or agreed to in writing, software
* distributed under the License is distributed on an "AS IS" BASIS,
* WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
* See the License for the specific language governing permissions and
* limitations under the License.
*/

'use strict';

const { WorkloadModuleBase } = require('@hyperledger/caliper-core');
const fs = require('fs');
const path = require('path');

/**
 * Workload module that reads transaction parameters from a file
 */
class ConflictTransactionGenerator extends WorkloadModuleBase {
    /**
     * Initializes the workload module instance.
     */
    constructor() {
        super();
        this.txIndex = 0;
        this.transactionPlan = [];
        this.conflictCount = 0;
        this.nonConflictCount = 0;
    }

    /**
     * Initialize the workload module with the given parameters.
     */
    async initializeWorkloadModule(workerIndex, totalWorkers, roundIndex, roundArguments, sutAdapter, sutContext) {
        await super.initializeWorkloadModule(workerIndex, totalWorkers, roundIndex, roundArguments, sutAdapter, sutContext);
        
        // Read transaction parameters from file
        const filePath = roundArguments.paramFile || 'conflictTX.txt';
        this.transactionPlan = this.readTransactionParamsFromFile(filePath);
        
        console.log(`Loaded ${this.transactionPlan.length} transactions from ${filePath}`);
    }

    /**
     * Read transaction parameters from file
     */
    readTransactionParamsFromFile(filePath) {
        try {
            const fullPath = path.isAbsolute(filePath) ? filePath : path.join(__dirname, filePath);
            const data = fs.readFileSync(fullPath, 'utf8');
            const lines = data.split('\n').filter(line => line.trim() !== '');
            
            return lines.map((line, index) => {
                const params = line.split(',');
                if (params.length < 2) {
                    throw new Error(`Invalid parameter format at line ${index + 1}: ${line}`);
                }
                
                return {
                    carNumber: params[0].trim(),
                    newOwner: params[1].trim(),
                    lineNumber: index + 1
                };
            });
        } catch (error) {
            console.error(`Error reading transaction parameters from file: ${error.message}`);
            throw error;
        }
    }

    /**
     * Assemble TXs for the round using parameters from file
     */
    async submitTransaction() {
        if (this.txIndex >= this.transactionPlan.length) {
            throw new Error('All transactions from file have been processed');
        }

        const transaction = this.transactionPlan[this.txIndex];

        let args = {
            contractId: 'fabcar',
            contractVersion: 'v1',
            contractFunction: 'changeCarOwner',
            contractArguments: [transaction.carNumber, transaction.newOwner],
            timeout: 60,
            metadata: {
                lineNumber: transaction.lineNumber,
                fromFile: true
            }
        };

        console.log(`Tx ${this.txIndex + 1}: Changing owner of ${transaction.carNumber} to ${transaction.newOwner}`);
        
        this.txIndex++;
        return await this.sutAdapter.sendRequests(args);
    }

    /**
     * Clean up and report statistics
     */
    async cleanupWorkloadModule() {
        console.log(`\n=== Transaction Execution Report ===`);
        console.log(`Total transactions processed: ${this.transactionPlan.length}`);
        
        await super.cleanupWorkloadModule();
    }
}

/**
 * Create a new instance of the workload module.
 */
function createWorkloadModule() {
    return new ConflictTransactionGenerator();
}

module.exports.createWorkloadModule = createWorkloadModule;