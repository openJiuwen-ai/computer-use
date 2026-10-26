using System;
using System.Collections.Generic;
using System.Linq;

namespace ComputerUse.Windows
{
    internal static class NativeQuerySchema
    {
        static readonly string[] FollowRelations =
        {
            "RawViewChildren",
            "LabeledBy",
            "Selection.Selection",
            "Table.RowHeaders",
            "Table.ColumnHeaders",
            "TableItem.RowHeaderItems",
            "TableItem.ColumnHeaderItems",
            "Text.Children",
            "ItemContainer.Items",
            "Grid.Cells",
            "DescribedBy",
            "FlowsTo",
            "FlowsFrom"
        };
        static string[] Names(Dictionary<string, object> query, string key, int maximum)
        {
            var raw = Wire.Array(query[key]);
            if (raw.Length < 1 || raw.Length > maximum || raw.Any(x => !(x is string) || String.IsNullOrWhiteSpace((string)x)) || raw.Distinct().Count() != raw.Length)
                throw new Failure("invalid_request", "invalid native property list");
            return raw.Cast<string>().ToArray();
        }

        internal static void Validate(Dictionary<string, object> query)
        {
            string kind = Wire.Str(query, "kind");
            switch (kind)
            {
                case "applications":
                    Wire.Keys(query, "kind");
                    break;
                case "surfaces":
                    Wire.Keys(query, "kind,application", "includeAccessibility");
                    Wire.Str(query, "application");
                    if (query.ContainsKey("includeAccessibility"))
                        Wire.Bool(query, "includeAccessibility");
                    break;
                case "nodes":
                    Wire.Keys(query, "kind,fields,maxNodes,maxDepth", "childrenPage,relationPage,advertisedFields,followRelations,focusedFields,primaryFields");
                    var fields = Names(query, "fields", 128);
                    if (query.ContainsKey("advertisedFields") && Names(query, "advertisedFields", 128).Except(fields).Any())
                        throw new Failure("invalid_request", "advertised properties must belong to the requested fields");
                    if (query.ContainsKey("followRelations") && Names(query, "followRelations", 16).Except(FollowRelations).Any())
                        throw new Failure("invalid_request", "unsupported native traversal relation");
                    if (query.ContainsKey("focusedFields") && (Names(query, "focusedFields", 1).Except(fields).Any() || Names(query, "focusedFields", 1).Any(name => name != "Text.CaretRange")))
                        throw new Failure("invalid_request", "focused fields must request Text.CaretRange");
                    if (query.ContainsKey("primaryFields") && (Names(query, "primaryFields", 1).Except(fields).Any() || Names(query, "primaryFields", 1).Any(name => name != "Text.LineRanges")))
                        throw new Failure("invalid_request", "primary fields must request Text.LineRanges");
                    double nodes = Wire.Num(query, "maxNodes", 1, 2000, true), depth = Wire.Num(query, "maxDepth", 0, 2000, true);
                    if (query.ContainsKey("childrenPage") && query.ContainsKey("relationPage"))
                        throw new Failure("invalid_request", "select one root relation page");
                    string pageKey = query.ContainsKey("relationPage") ? "relationPage" : "childrenPage";
                    if (query.ContainsKey(pageKey))
                    {
                        var page = Wire.Object(query[pageKey]);
                        Wire.Keys(page, pageKey == "relationPage" ? "property,cursor,limit" : "cursor,limit");
                        if (pageKey == "relationPage")
                            Wire.Str(page, "property");
                        if (page["cursor"] != null)
                            Wire.Str(page, "cursor");
                        if (Wire.Num(page, "limit", 1, 1999, true) >= nodes || depth < 1)
                            throw new Failure("invalid_request", "relation page requires depth and space for its root and targets");
                    }

                    break;
                case "relation":
                    Wire.Keys(query, "kind,property,cursor,limit");
                    Wire.Str(query, "property");
                    if (query["cursor"] != null)
                        Wire.Str(query, "cursor");
                    Wire.Num(query, "limit", 1, 2000, true);
                    break;
                case "inputContext":
                    Wire.Keys(query, "kind,within", "currentReceiver");
                    var within = Wire.Array(query["within"]);
                    if (within.Length > 16 || within.Any(x => !(x is string) || String.IsNullOrWhiteSpace((string)x)) || within.Distinct().Count() != within.Length)
                        throw new Failure("invalid_request", "invalid ancestor constraint list");
                    if (query.ContainsKey("currentReceiver") && (!Wire.Bool(query, "currentReceiver") || within.Length != 0))
                        throw new Failure("invalid_request", "current receiver discovery requires an unselected element scope");
                    break;
                case "readChange":
                    Wire.Keys(query, "kind,after,timeoutMs");
                    Wire.Str(query, "after");
                    Wire.Num(query, "timeoutMs", 1, 400, true);
                    break;
                case "capturedText":
                    Wire.Keys(query, "kind,textRef,offset,length");
                    Wire.Str(query, "textRef");
                    Wire.Num(query, "offset", 0, 9007199254740991, true);
                    Wire.Num(query, "length", 2, 16384, true);
                    break;
                case "launch":
                case "roots":
                    throw new Failure("mode_unsupported", "this native Windows slice provides window discovery and raw UIA reading only");
                default:
                    throw new Failure("invalid_request", "unknown native observation query");
            }
        }
    }
}
