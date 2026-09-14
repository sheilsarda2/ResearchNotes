// Go reference indexed reader for the cpp-mcap-indexed-reader verifier.
//
// This is go/conformance/test-read-conformance/main.go (readIndexed mode) from the base commit,
// with one behavioural fix: the upstream tool dereferences the *Schema returned by
// indexedMessageIterator.NextInto unconditionally, but the library returns a nil schema for
// channels with schema_id 0 (schema-less channels), so the upstream tool panics on any such file.
// The TypeScript conformance harness avoids this by never feeding it such inputs. Every generated
// fixture of this task has a schema-less channel, so the guard below is required.
//
// It is compiled with `go build -overlay` on top of the pristine test-read-conformance module so
// that it links exactly the same go/mcap library (and the same dependency versions) as the
// upstream tool. The output shape (IndexedReadTestResult JSON, all scalars serialized as strings,
// message data as an array of decimal byte strings) is identical to the upstream tool's, which is
// what run_checks.py's sibling comparison consumes.
package main

import (
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"reflect"
	"regexp"
	"sort"
	"strings"

	"github.com/foxglove/mcap/go/mcap"
)

var (
	matchFirstCap = regexp.MustCompile("(.)([A-Z][a-z]+)")
	matchAllCap   = regexp.MustCompile("([a-z0-9])([A-Z])")
)

func toSnakeCase(s string) string {
	snake := matchFirstCap.ReplaceAllString(s, "${1}_${2}")
	snake = matchAllCap.ReplaceAllString(snake, "${1}_${2}")
	return strings.ToLower(snake)
}

type Field struct {
	Name  string
	Value any
}

func (x Field) MarshalJSON() ([]byte, error) {
	t := reflect.TypeOf(x.Value)
	var v any
	switch t.Name() {
	case "string":
		v = fmt.Sprintf("%q", x.Value)
	case "uint8", "uint16", "uint32", "uint64":
		v = fmt.Sprintf("\"%d\"", x.Value)
	case "OpCode":
		v = fmt.Sprintf("\"%d\"", x.Value)
	case "CompressionFormat":
		v = fmt.Sprintf("%q", x.Value)
	default:
		switch t.Kind() {
		case reflect.Map:
			keyType := t.Key().Kind()
			valueType := t.Elem().Kind()
			m := make(map[string]string)
			switch {
			case keyType == reflect.String && valueType == reflect.String:
				for k, v := range x.Value.(map[string]string) {
					m[k] = v
				}
			case keyType == reflect.Uint16 && valueType == reflect.Uint32:
				for k, v := range x.Value.(map[uint16]uint32) {
					m[fmt.Sprintf("%d", k)] = fmt.Sprintf("%d", v)
				}
			case keyType == reflect.Uint16 && valueType == reflect.Uint64:
				for k, v := range x.Value.(map[uint16]uint64) {
					m[fmt.Sprintf("%d", k)] = fmt.Sprintf("%d", v)
				}
			default:
				return nil, fmt.Errorf("unrecognized types: %s, %s", keyType, valueType)
			}
			bytes, err := json.Marshal(m)
			if err != nil {
				return nil, err
			}
			v = string(bytes)
		case reflect.Slice:
			switch elemType := t.Elem(); elemType.Name() {
			case "uint8":
				val := x.Value.([]uint8)
				ints := make([]string, len(val))
				for i, v := range val {
					ints[i] = fmt.Sprintf("%d", v)
				}
				bytes, err := json.Marshal(ints)
				if err != nil {
					return nil, fmt.Errorf("failed to marshal []uint8: %w", err)
				}
				v = string(bytes)
			case "MessageIndexEntry":
				results := [][]string{}
				entries := x.Value.([]mcap.MessageIndexEntry)
				for _, entry := range entries {
					results = append(results, []string{
						fmt.Sprintf("\"%d\"", entry.Timestamp),
						fmt.Sprintf("\"%d\"", entry.Offset),
					})
				}
				bytes, err := json.Marshal(results)
				if err != nil {
					return nil, fmt.Errorf("failed to marshal MessageIndexEntry: %w", err)
				}
				v = string(bytes)
			default:
				return nil, fmt.Errorf("unrecognized slice type: %s", elemType.Name())
			}
		default:
			v = x.Value
		}
	}
	return []byte(fmt.Sprintf(`[%q, %s]`, x.Name, v)), nil
}

type Record struct {
	V any
}

func (r Record) MarshalJSON() ([]byte, error) {
	t := reflect.TypeOf(r.V)
	v := reflect.ValueOf(r.V)
	fields := make([]Field, 0, v.NumField())
	for i := 0; i < v.NumField(); i++ {
		if name := toSnakeCase(t.Field(i).Name); name != "crc" {
			if v.Field(i).CanInterface() {
				fields = append(fields, Field{Name: name, Value: v.Field(i).Interface()})
			}
		}
	}
	sort.Slice(fields, func(i, j int) bool { return fields[i].Name < fields[j].Name })
	record := struct {
		Type   string  `json:"type"`
		Fields []Field `json:"fields"`
	}{Type: t.Name(), Fields: fields}
	return json.Marshal(record)
}

// readIndexed mirrors the upstream conformance runner: NewReader, Messages(InOrder(LogTimeOrder))
// (index-driven by default), first Schema/Channel per id, Statistics from Info().
func readIndexed(w io.Writer, filepath string) error {
	f, err := os.Open(filepath)
	if err != nil {
		return err
	}
	defer f.Close()
	result := struct {
		Messages   []Record `json:"messages"`
		Schemas    []Record `json:"schemas"`
		Channels   []Record `json:"channels"`
		Statistics []Record `json:"statistics"`
	}{
		Messages:   make([]Record, 0),
		Schemas:    make([]Record, 0),
		Channels:   make([]Record, 0),
		Statistics: make([]Record, 0),
	}

	reader, err := mcap.NewReader(f)
	if err != nil {
		return err
	}
	it, err := reader.Messages(mcap.InOrder(mcap.LogTimeOrder))
	if err != nil {
		return err
	}
	info, err := reader.Info()
	if err != nil {
		return err
	}
	if info.Statistics != nil {
		result.Statistics = append(result.Statistics, Record{*info.Statistics})
	}

	knownSchemaIDs := make(map[uint16]bool)
	knownChannelIDs := make(map[uint16]bool)

	for {
		schema, channel, message, err := it.NextInto(nil)
		if errors.Is(err, io.EOF) {
			break
		}
		if err != nil {
			return err
		}
		// Fix versus upstream: schema is nil for schema-less channels (schema_id 0).
		if schema != nil {
			if _, found := knownSchemaIDs[schema.ID]; !found {
				knownSchemaIDs[schema.ID] = true
				result.Schemas = append(result.Schemas, Record{*schema})
			}
		}
		if channel == nil {
			return fmt.Errorf("message on channel %d without a Channel record", message.ChannelID)
		}
		if _, found := knownChannelIDs[channel.ID]; !found {
			knownChannelIDs[channel.ID] = true
			result.Channels = append(result.Channels, Record{*channel})
		}
		result.Messages = append(result.Messages, Record{*message})
	}
	serializedOutput, err := json.Marshal(result)
	if err != nil {
		return err
	}
	_, err = w.Write(serializedOutput)
	return err
}

func main() {
	if len(os.Args) < 2 {
		fmt.Fprintln(os.Stderr, "usage: go-indexed-reader <file.mcap> [indexed]")
		os.Exit(2)
	}
	if err := readIndexed(os.Stdout, os.Args[1]); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}
