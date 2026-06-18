package main

import (
	"encoding/json"
	"fmt"
	"strconv"

	"github.com/hyperledger/fabric-chaincode-go/shim"
	pb "github.com/hyperledger/fabric-protos-go/peer"
)

type TokenChaincode struct {
	name     string
	symbol   string
	decimals uint8
}

func (c *TokenChaincode) Init(stub shim.ChaincodeStubInterface) pb.Response {
	_, args := stub.GetFunctionAndParameters()
	if len(args) >= 3 {
		c.name = args[0]
		c.symbol = args[1]
		d, err := strconv.Atoi(args[2])
		if err == nil {
			c.decimals = uint8(d)
		}
	}
	return shim.Success(nil)
}

func (c *TokenChaincode) Invoke(stub shim.ChaincodeStubInterface) pb.Response {
	fn, args := stub.GetFunctionAndParameters()
	switch fn {
	case "mint":
		return c.mint(stub, args)
	case "transfer":
		return c.transfer(stub, args)
	case "balance_of":
		return c.balanceOf(stub, args)
	case "totalSupply":
		return c.totalSupply(stub)
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

func (c *TokenChaincode) mint(stub shim.ChaincodeStubInterface, args []string) pb.Response {
	if len(args) < 2 {
		return shim.Error("mint requires account and amount")
	}
	account := args[0]
	amount, err := strconv.ParseUint(args[1], 10, 64)
	if err != nil || amount == 0 {
		return shim.Error("invalid amount")
	}

	balKey := "balance:" + account
	current := c.getBalance(stub, balKey)
	newBal := current + amount

	if err := stub.PutState(balKey, []byte(fmt.Sprintf("%d", newBal))); err != nil {
		return shim.Error(err.Error())
	}
	return shim.Success(nil)
}

func (c *TokenChaincode) transfer(stub shim.ChaincodeStubInterface, args []string) pb.Response {
	if len(args) < 3 {
		return shim.Error("transfer requires from, to, amount")
	}
	from := args[0]
	to := args[1]
	amount, err := strconv.ParseUint(args[2], 10, 64)
	if err != nil || amount == 0 {
		return shim.Error("invalid amount")
	}

	fromKey := "balance:" + from
	toKey := "balance:" + to

	fromBal := c.getBalance(stub, fromKey)
	if fromBal < amount {
		return shim.Error("insufficient balance")
	}

	if err := stub.PutState(fromKey, []byte(fmt.Sprintf("%d", fromBal-amount))); err != nil {
		return shim.Error(err.Error())
	}
	toBal := c.getBalance(stub, toKey)
	if err := stub.PutState(toKey, []byte(fmt.Sprintf("%d", toBal+amount))); err != nil {
		return shim.Error(err.Error())
	}
	return shim.Success(nil)
}

func (c *TokenChaincode) balanceOf(stub shim.ChaincodeStubInterface, args []string) pb.Response {
	if len(args) < 1 {
		return shim.Error("balance_of requires account")
	}
	balKey := "balance:" + args[0]
	bal := c.getBalance(stub, balKey)
	return shim.Success([]byte(fmt.Sprintf("%d", bal)))
}

func (c *TokenChaincode) totalSupply(stub shim.ChaincodeStubInterface) pb.Response {
	total := c.getBalance(stub, "totalSupply")
	return shim.Success([]byte(fmt.Sprintf("%d", total)))
}

func (c *TokenChaincode) getBalance(stub shim.ChaincodeStubInterface, key string) uint64 {
	val, err := stub.GetState(key)
	if err != nil || val == nil {
		return 0
	}
	n, err := strconv.ParseUint(string(val), 10, 64)
	if err != nil {
		return 0
	}
	return n
}

func main() {
	if err := shim.Start(new(TokenChaincode)); err != nil {
		fmt.Printf("Error starting token chaincode: %s\n", err)
	}
}

var _ shim.Chaincode = (*TokenChaincode)(nil)
var _ = json.Marshal
