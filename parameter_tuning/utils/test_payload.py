TEST = """
smallbankArgs: &smallbank-args
  accountsGenerated: 100
  txnPerBatch: 1

rateControl: &rate
  type: fixed-rate
  opts:
    tps: {}

test:
  name: smallbank
  description: Smallbank benchmark for evaluating create, modify, and query operations.
  workers:
    number: 5
  rounds:
    - label: create
      txNumber: {}
      rateControl: *rate
      workload:
        module: benchmarks/scenario/smallbank/create.js
        arguments: *smallbank-args
""".strip()
