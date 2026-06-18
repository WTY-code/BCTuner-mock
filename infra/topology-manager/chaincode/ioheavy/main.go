package main

import (
	"encoding/json"
	"fmt"
	"strconv"

	"github.com/hyperledger/fabric-chaincode-go/shim"
	pb "github.com/hyperledger/fabric-protos-go/peer"
)

type IoheavyChaincode struct{}

func (c *IoheavyChaincode) Init(stub shim.ChaincodeStubInterface) pb.Response {
	return shim.Success(nil)
}

func (c *IoheavyChaincode) Invoke(stub shim.ChaincodeStubInterface) pb.Response {
	fn, args := stub.GetFunctionAndParameters()
	switch fn {
	case "batch_put":
		return c.batchPut(stub, args)
	case "batch_get":
		return c.batchGet(stub, args)
	case "get_state":
		if len(args) < 1 {
			return shim.Error("get_state requires key")
		}
		val, err := stub.GetState(args[0])
		if err != nil {
			return shim.Error(err.Error())
		}
		if val == nil {
			return shim.Error("not found")
		}
		return shim.Success(val)
	default:
		return shim.Error(fmt.Sprintf("unknown function: %s", fn))
	}
}

func (c *IoheavyChaincode) batchPut(stub shim.ChaincodeStubInterface, args []string) pb.Response {
	if len(args) < 3 {
		return shim.Error("batch_put requires start, num, prefix")
	}
	start, err := strconv.Atoi(args[0])
	if err != nil {
		return shim.Error("invalid start")
	}
	num, err := strconv.Atoi(args[1])
	if err != nil {
		return shim.Error("invalid num")
	}
	prefix := args[2]

	for i := 0; i < num; i++ {
		key := fmt.Sprintf("k:%s_%d", prefix, start+i)
		if _, err := stub.GetState(key); err != nil {
			return shim.Error(err.Error())
		}
		val := fmt.Sprintf("v_%s_%d", prefix, start+i)
		if err := stub.PutState(key, []byte(val)); err != nil {
			return shim.Error(err.Error())
		}
	}
	return shim.Success(nil)
}

func (c *IoheavyChaincode) batchGet(stub shim.ChaincodeStubInterface, args []string) pb.Response {
	if len(args) < 3 {
		return shim.Error("batch_get requires start, num, prefix")
	}
	start, err := strconv.Atoi(args[0])
	if err != nil {
		return shim.Error("invalid start")
	}
	num, err := strconv.Atoi(args[1])
	if err != nil {
		return shim.Error("invalid num")
	}
	prefix := args[2]

	var results []string
	for i := 0; i < num; i++ {
		key := fmt.Sprintf("k:%s_%d", prefix, start+i)
		val, err := stub.GetState(key)
		if err != nil {
			return shim.Error(err.Error())
		}
		results = append(results, string(val))
	}
	return shim.Success([]byte(fmt.Sprintf("%v", results)))
}

func main() {
	if err := shim.Start(new(IoheavyChaincode)); err != nil {
		fmt.Printf("Error starting ioheavy chaincode: %s\n", err)
	}
}

var _ shim.Chaincode = (*IoheavyChaincode)(nil)
var _ = json.Marshal
