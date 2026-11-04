// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

using System;
using UIA = NativeUIA;
using System.Collections.Generic;
using System.Linq;
using System.Runtime.CompilerServices;
using System.Text;
using System.Windows.Automation;

namespace ComputerUse.Windows
{
    internal sealed class NativeFieldUnavailable : Exception
    {
        internal readonly string State;
        internal NativeFieldUnavailable(string state, string detail = "") : base(detail)
        {
            State = state;
        }

        internal NativeFieldUnavailable(string state, string detail, object rawResult) : base(detail)
        {
            State = state;
            Data["nativeRawResult"] = rawResult;
        }
    }

    // Raw UIA properties and relations. Projection, menu selection and modal
    // following belong to Python; no observation calls v3 Backend.ReadApp.
    internal sealed class NativeReadBackend : INativeReadBackend, INativeReadPreparation, INativeReadEventBackend
    {
        readonly string session;
        readonly NativeReadStore store;
        readonly NativeRootProbe rootProbe = new NativeRootProbe();
        readonly NativeCapturedText texts = new NativeCapturedText();
        internal NativeReadStore Store
        {
            get {
                return store;
            }
        }

        internal bool NeedsRestart
        {
            get {
                return rootProbe.Blocked || NativeWindowDiscovery.RequiresRestart || events.NeedsRestart;
            }
        }

        sealed class Page
        {
            internal string Surface, Node, Property;
            internal string[] Targets;
            internal int Offset;
            internal bool Complete, Captured;
            internal Dictionary<string, object> MembershipObservedAt, NativeStatus;
            internal object[] MemberIssues;
            internal string Detail;
        }

        readonly Dictionary<string, Page> pages = new Dictionary<string, Page>();
        readonly Queue<string> pageOrder = new Queue<string>();
        // Owning containers are facts, not automatic traversal back into an
        // entire table or a source of input authority.
        static readonly string[] Relations = { "RawViewChildren", "RawViewParent", "LabeledBy",
                                               "GridItem.ContainingGrid", "SelectionItem.SelectionContainer" };
        Request request;
        double until;
        NativeReadCache cache;
        sealed class TextSourceResolution
        {
            internal NativeReadText Reader;
            internal Dictionary<string, object> Failure;
        }
        readonly Dictionary<string, TextSourceResolution> textReaders = new Dictionary<string, TextSourceResolution>();
        readonly Dictionary<string, Dictionary<string, object>> nativeValues =
            new Dictionary<string, Dictionary<string, object>>();
        readonly Dictionary<UiaTextRange, string> rangeIds = new Dictionary<UiaTextRange, string>();
        sealed class NativeObjectIdentity : IEqualityComparer<object>
        {
            public new bool Equals(object a, object b)
            {
                return Object.ReferenceEquals(a, b);
            }

            public int GetHashCode(object value)
            {
                return RuntimeHelpers.GetHashCode(value);
            }
        }

        readonly Dictionary<object, string> nativeValuesByObject =
            new Dictionary<object, string>(new NativeObjectIdentity());
        const int NativeValueLimit = 256, NativeRangeLimit = 4096;
        readonly Dictionary<string, Dictionary<string, object>> rangeNodes =
            new Dictionary<string, Dictionary<string, object>>();
        readonly HashSet<string> focusedFields = new HashSet<string>();
        readonly HashSet<string> primaryFields = new HashSet<string>();
        readonly List<string> preparationNotes = new List<string>();
        readonly List<object> cachedFailures = new List<object>();
        readonly NativeReadEvents events = new NativeReadEvents();
        List<string> observationNotes;
        readonly Action<string> trace;
        internal NativeReadBackend(string session, Action<string> trace = null,
                                   NativeReadStore retainedStore = null)
        {
            this.session = session;
            this.trace = trace;
            store = retainedStore ?? new NativeReadStore();
        }

        public bool NeedsPreparation(Dictionary<string, object> target)
        {
            return target["surface"] != null;
        }

        public void PrepareRead(Dictionary<string, object> target, Request r, bool activate)
        {
            if (!NeedsPreparation(target))
                return;
            var selected = store.Validate(target, r, true);
            if (!Native.IsWindowVisible(selected.Native.Handle))
                throw new Failure("observation_unavailable",
                                  "the selected window is hidden; it was not replaced with another window",
                                  "show the intended window and read it again");
            // Pixel-only overviews need not activate every normal window.
            // Minimized windows must be restored before either observation;
            // a selected UIA read additionally prepares the foreground.
            bool noActivate = Native.NoActivate(selected.Native.Handle);
            if (noActivate && Native.IsIconic(selected.Native.Handle))
            {
                r.Check();
                selected.Native.Validate();
                r.Effect = true;
                Native.ShowWindowAsync(selected.Native.Handle, 4); // SW_SHOWNOACTIVATE
            }

            if (!noActivate && (activate || Native.IsIconic(selected.Native.Handle)))
            {
                try
                {
                    Native.Foreground(selected.Native, true, r, activate,
                                      () => store.Validate(target, r, true));
                }
                catch (Failure error)
                {
                    // Foreground is useful for hydration, not an authority
                    // requirement for reading an already restored window.
                    // Keep the failure visible and never grant input readiness.
                    r.Check();
                    store.Validate(target, r, true);
                    if (!activate || error.Code != "input_unavailable" || Native.IsIconic(selected.Native.Handle) ||
                        !Native.IsWindowVisible(selected.Native.Handle))
                        throw;
                    if (r.Effect)
                        Native.NoteVisualInput();
                    NativeReadStore.Note(
                        preparationNotes,
                        "Foreground preparation was unavailable; reading the same restored window in background. Input readiness is not established: " +
                            error.Message);
                }
            }

            // Restore/activation can recreate provider objects and change
            // geometry. Wait for native restoration, then collect fresh UIA
            // fields/pixels; never turn a successful API call into readiness.
            double until = Math.Min(r.Deadline, Wire.Now + 1000);
            while (Native.IsIconic(selected.Native.Handle) && Wire.Now < until)
            {
                r.Wait(20);
                selected.Native.Validate();
            }

            store.Validate(target, r, true);
            if (Native.IsIconic(selected.Native.Handle) || !Native.IsWindowVisible(selected.Native.Handle))
                throw new Failure(
                    "observation_unavailable",
                    "the selected window did not finish restoring; its content was not read as an empty window",
                    "read the intended window again after it becomes visible");
        }

        void Trace(string value)
        {
            if (trace != null)
                trace(value);
        }

        void RecordCachedFailure(string key, int property, Exception error)
        {
            if (cachedFailures.Count >= 128)
            {
                NativeReadStore.Note(
                    observationNotes,
                    "Cached-property diagnostic budget exhausted; additional failed cached attempts remain unexpanded.");
                return;
            }

            var evidence = Status(error);
            evidence["cached"] = true;
            evidence["api"] = "IUIAutomationElement.GetCachedPropertyValueEx";
            evidence["propertyId"] = property;
            evidence["cacheKey"] = key;
            cachedFailures.Add(evidence);
        }

        public void Validate(Dictionary<string, object> target, Request r)
        {
            store.Validate(target, r, true);
        }

        public void Release()
        {
            cache = null;
            textReaders.Clear();
            preparationNotes.Clear();
            cachedFailures.Clear();
            nativeValues.Clear();
            rangeIds.Clear();
            nativeValuesByObject.Clear();
            rangeNodes.Clear();
            store.Release();
            NativeWindowDiscovery.Release();
        }

        public void Dispose()
        {
            try
            {
                events.Dispose();
            }
            finally
            {
                NativeWindowDiscovery.Close();
            }
        }

        static Dictionary<string, object> Stamp(double start)
        {
            return Wire.Obj("id", NativeReadStore.Token("observation"), "startedAt", Math.Floor(start), "completedAt",
                            Math.Max(Math.Floor(start), Math.Ceiling(Wire.Now)));
        }

        void Check()
        {
            request.Check();
            if (Wire.Now >= until)
                throw new NativeFieldUnavailable("timeout", "bounded UIA observation budget exhausted");
        }

        object Get(UiaElement e, AutomationProperty property)
        {
            Check();
            object value = cache == null ? e.GetCurrentPropertyValue(property, true) : cache.Get(e, property);
            request.Check();
            if (ReferenceEquals(value, AutomationElement.NotSupported))
            {
                // UIA defines the NativeWindowHandle default as zero. Match
                // the typed Current.NativeWindowHandle contract used by live
                // containment checks; zero reports no provider HWND and does
                // not itself grant input authority or prove native ownership.
                if (property == AutomationElement.NativeWindowHandleProperty)
                    return 0;
                throw new NativeFieldUnavailable("unsupported");
            }

            return value;
        }

        object Get(UiaElement e, int property)
        {
            Check();
            object value = cache == null ? e.GetCurrentPropertyValue(property, true) : cache.Get(e, property);
            request.Check();
            if (ReferenceEquals(value, AutomationElement.NotSupported))
                throw new NativeFieldUnavailable("unsupported");
            return value;
        }

        UiaClient.Pattern[] Patterns(UiaElement e)
        {
            Check();
            var result = cache == null ? e.GetSupportedPatterns() : cache.Patterns(e);
            Check();
            return result;
        }
        NativeReadText TextReader(NativeNodeRecord node, string field = null)
        {
            // Availability was captured with the node's properties. Avoid
            // repeated cross-process GetCurrentPattern failures for every text
            // field/relation on controls that do not expose TextPattern.
            // Do not collect protected text. Unknown protection state remains
            // unknown, rather than being silently interpreted as false.
            object password = Get(node.Element, AutomationElement.IsPasswordProperty);
            if (!(password is bool))
                throw new NativeFieldUnavailable("error", "UIA password protection state unavailable", password);
            if ((bool)password)
                throw new NativeFieldUnavailable("unsupported", "protected text is not captured");
            var supported = Patterns(node.Element);
            bool nativeText = supported.Any(p => p.Id == TextPattern.Pattern.Id);
            bool nativeTextChild = supported.Any(p => p.Id == (int)UIA.UIA_PatternIds.UIA_TextChildPatternId);
            if (!nativeText && nativeTextChild && NativeReadTextChildSource.RequiresOwnTextPattern(field))
                throw new NativeFieldUnavailable(
                    "unsupported",
                    "TextChildPattern exposes this child's exact range, not its own TextPattern selection, visibility or position capabilities; container state was not substituted");
            TextSourceResolution resolved;
            if (!textReaders.TryGetValue(node.Id, out resolved))
            {
                try
                {
                    resolved = new TextSourceResolution { Reader = new NativeReadText(
                                                              node.Element, nativeText,
                                                              supported.Any(p => p.Id == ValuePattern.Pattern.Id),
                                                              Check, trace, nativeTextChild) };
                }
                catch (Exception error)
                {
                    // Source resolution is sampled once per node/observation.
                    // Terminal caller boundaries propagate before any outcome
                    // is cached; a later Inspect starts with a fresh map.
                    var failure = Status(error);
                    request.Check();
                    resolved = new TextSourceResolution { Failure = failure };
                    textReaders[node.Id] = resolved;
                    throw;
                }
                textReaders[node.Id] = resolved;
            }
            if (resolved.Failure != null)
                throw new NativeFieldUnavailable((string)resolved.Failure["state"], (string)resolved.Failure["detail"]);
            return resolved.Reader;
        }

        object Field(NativeNodeRecord node, string name, int textLimit = NativeCapturedText.PreviewUnits + 1)
        {
            Trace("field " + node.Id + " " + name);
            var e = node.Element;
            if (name == "Win32.ClientRectangle")
            {
                if (new IntPtr(Convert.ToInt64(Get(e, AutomationElement.NativeWindowHandleProperty))) !=
                    node.Window.Native.Handle)
                    throw new NativeFieldUnavailable("absent", "not the selected native window root");
                node.Window.Native.Validate();
                Native.RECT bounds;
                var origin = new Native.POINT();
                if (!Native.GetClientRect(node.Window.Native.Handle, out bounds) ||
                    !Native.ClientToScreen(node.Window.Native.Handle, ref origin))
                    throw new NativeFieldUnavailable("error", "native client rectangle unavailable");
                return Wire.Obj("x", origin.X, "y", origin.Y, "width", bounds.Right - bounds.Left, "height",
                                bounds.Bottom - bounds.Top);
            }

            if (name == "UIA.ReadSource")
                return "IUIAutomation; RawView; native provider fragments; legacy HWND text may use an identity-checked read-only .NET proxy";
            if (name == "UIA.ReferenceScope")
                return node.ReferenceScope;
            if (name == "UIA.ReferenceScopeEvidence")
                return node.ReferenceScopeEvidence;
            if (name == "Win32.WindowContentScope")
            {
                // Window is a UIA role, not a top-level HWND contract. Resolve
                // the nearest native host without using title or process name.
                if (Get(e, AutomationElement.ControlTypeProperty) as ControlType != ControlType.Window)
                    throw new NativeFieldUnavailable(
                        "unsupported", "native host scope is only requested for Window content boundaries");
                Check();
                node.Window.Native.Validate();
                IntPtr host = store.NativeHandle(e, request);
                Check();
                node.Window.Native.Validate();
                return host == IntPtr.Zero                              ? "unknown"
                       : Native.Inside(node.Window.Native.Handle, host) ? "content"
                                                                        : "independent";
            }

            if (name == "UIA.Collection")
            {
                var patterns = Patterns(e);
                return Wire.Obj(
                    "view", "raw", "mayContainVirtualizedItems",
                    patterns.Any(p => p.Id == ItemContainerPattern.Pattern.Id), "isVirtualizedItem",
                    patterns.Any(p => p.Id == VirtualizedItemPattern.Pattern.Id), "logicalContentComplete", null,
                    "detail",
                    "Raw children describe exposed objects, not all logical items or pixels. No realization or scrolling performed.");
            }
            if (name == "Text.ObjectRanges")
            {
                if (node.Outside)
                    throw new NativeFieldUnavailable(
                        "unsupported",
                        "The observed object is outside the selected window; embedded-object relations are not expanded. Select its independent window to explore it.");
                return TextReader(node, name).ObjectRanges(child => store.KeepRelated(child, node, request));
            }
            if (name == "Text.LineRanges")
                return TextReader(node, name).LineRanges();
            if (name == "Text.CaretRange")
            {
                if (!Patterns(e).Any(pattern => pattern.Id == 10024))
                    throw new NativeFieldUnavailable("unsupported", "UIA does not expose TextPattern2");
                return TextReader(node, name).CaretRange(focusedFields.Contains(name));
            }

            if (name.StartsWith("Text.", StringComparison.Ordinal))
                return TextReader(node, name).Field(name, textLimit);
            RequirePattern(e, name);
            if (name == "Value.Value")
            {
                object password = Get(e, AutomationElement.IsPasswordProperty);
                if (!(password is bool) || (bool)password)
                    throw new NativeFieldUnavailable("unsupported",
                                                     "protected or unknown-protection text is not captured");
            }

            if (name == "SupportedPatterns")
            {
                var patterns = Patterns(e);
                RecordNative(node.Id, name,
                             new NativeObservationValue(
                                 "UIA.PATTERNID[]",
                                 patterns
                                     .Select(p => new NativeObservationValue(
                                                 "UIA.PATTERNID", p.Id,
                                                 NativeObservationGraph.Pair("enum_name", p.ProgrammaticName)))
                                     .ToArray()));
                return patterns.Select(p => p.ProgrammaticName).ToArray();
            }

            AutomationProperty property;
            int id;
            object value;
            if (NativeReadSchema.Properties.TryGetValue(name, out property))
                value = name == "NativeWindowHandle" ? Get(e, property.Id) : Get(e, property);
            else if (NativeReadSchema.ReadingProperties.TryGetValue(name, out id))
                value = Get(e, id);
            else
                throw new NativeFieldUnavailable("unsupported", "unknown UIA property");
            var controlType = value as ControlType;
            if (controlType != null)
                RecordNative(
                    node.Id, name,
                    new NativeObservationValue("UIA.ControlType", controlType.Id,
                                               NativeObservationGraph.Pair("enum_name", controlType.ProgrammaticName)));
            else if (name == "ControlType" && value is string &&
                     ((string)value).StartsWith("ControlType.", StringComparison.Ordinal))
            {
                int rawType;
                if (Int32.TryParse(((string)value).Substring("ControlType.".Length), out rawType))
                    RecordNative(node.Id, name,
                                 new NativeObservationValue("UIA.ControlType", rawType,
                                                            NativeObservationGraph.Pair("enum_name", value)));
            }
            else if (value is Enum)
                RecordNative(node.Id, name,
                             new NativeObservationValue(value.GetType().FullName, Convert.ToInt64(value),
                                                        NativeObservationGraph.Pair("enum_name", value.ToString())));
            else if (value is System.Windows.Rect)
                RecordNative(node.Id, name, Geometry(NativeReadText.Bounds((System.Windows.Rect)value), "UIA.Rect"));
            if (controlType != null)
                return controlType.ProgrammaticName;
            if (value is System.Windows.Rect)
                return NativeReadText.Bounds((System.Windows.Rect)value);
            if (value is double)
                return NativeReadValues.Number((double)value);
            if (value is Enum)
                return value.ToString();
            return value;
        }

        void RecordNative(string node, string field, object value)
        {
            Dictionary<string, object> fields;
            if (!nativeValues.TryGetValue(node, out fields))
                nativeValues[node] = fields = new Dictionary<string, object>();
            fields[field] = value;
        }

        static NativeObservationValue Geometry(object data, string type)
        {
            return new NativeObservationValue(
                type, data, NativeObservationGraph.Pair("coordinate_space", "windows.uia.physical_screen"),
                NativeObservationGraph.Pair("unit", "physical_pixel"),
                NativeObservationGraph.Pair("coordinate_metadata_source", "api_contract"));
        }

        NativeObservationReference RetainOpaque(object value)
        {
            string id;
            if (!nativeValuesByObject.TryGetValue(value, out id))
            {
                if (nativeValuesByObject.Count >= NativeValueLimit)
                {
                    NativeReadStore.Note(
                        observationNotes,
                        "The bounded native-value retention budget is exhausted; unread resource positions are retained as typed issues.");
                    return null;
                }

                id = NativeReadStore.Token("native_value");
                nativeValuesByObject.Add(value, id);
            }

            if (!rangeNodes.ContainsKey(id))
                rangeNodes[id] = Wire.Obj(
                    "id", id, "source", "windows.uia", "kind", "native_value", "nativeType", value.GetType().FullName,
                    "properties", null, "read",
                    NativeObservationGraph.Read(
                        "IUIAutomation", false, null,
                        new[] {
                            NativeObservationGraph.Issue(
                                "native_value_not_expanded",
                                "The original native object is retained for this operation; no input authority or inferred properties are attached.")
                        }));
            return new NativeObservationReference(id);
        }

        object RangeReferences(NativeReadText reader, string field)
        {
            UiaTextRange[] ranges;
            if (!reader.ObservedRanges.TryGetValue(field, out ranges))
                return null;
            var references = new List<object>();
            foreach (var range in ranges)
            {
                if (range == null)
                {
                    references.Add(null);
                    continue;
                }

                string id;
                if (!rangeIds.TryGetValue(range, out id))
                {
                    if (rangeIds.Count >= NativeRangeLimit)
                    {
                        const string detail =
                            "The operation's native text-range retention budget is exhausted; the original resource position remains unread.";
                        references.Add(new NativeObservationValue(
                            "IUIAutomationTextRange",
                            new NativeObservationGap("native_resource_budget_exhausted", detail)));
                        NativeReadStore.Note(observationNotes, detail);
                        continue;
                    }

                    id = NativeReadStore.Token("text_range");
                    rangeIds[range] = id;
                }

                if (!rangeNodes.ContainsKey(id))
                    rangeNodes[id] = Wire.Obj("id", id, "source", "windows.uia", "kind", "text_range", "nativeType",
                                              reader.Source.StartsWith("IUIAutomation", StringComparison.Ordinal)
                                                  ? "IUIAutomationTextRange"
                                                  : "System.Windows.Automation.Text.TextPatternRange",
                                              "properties", null);
                references.Add(
                    new NativeObservationValue("IUIAutomationTextRange", new NativeObservationReference(id)));
            }

            return references.ToArray();
        }

        void RequirePattern(UiaElement e, string name)
        {
            AutomationPattern pattern;
            if (!NativeReadSchema.RequiredPatterns.TryGetValue(name, out pattern))
                return;
            if (!Patterns(e).Any(p => p.Id == pattern.Id))
                throw new NativeFieldUnavailable("unsupported", "UIA does not expose the required pattern");
        }

        internal static Dictionary<string, object> Status(Exception error)
        {
            var failure = error as Failure;
            if (failure != null && (failure.Code == "cancelled" || failure.Code == "deadline_exceeded"))
                throw failure;
            var unavailable = error as NativeFieldUnavailable;
            bool timedOut =
                error is TimeoutException || (uint)error.HResult == 0x80131505 || (uint)error.HResult == 0x8001011F;
            var result = Wire.Obj(
                "state", unavailable == null ? timedOut ? "timeout" : "error" : unavailable.State, "detail",
                NativeReadStore.Text(unavailable == null ? error.GetType().Name + " (0x" +
                                                               error.HResult.ToString("X8") + "): " + error.Message
                                                         : error.Message),
                "nativeError",
                Wire.Obj("type", error.GetType().FullName, "hresult", error.HResult, "message", error.Message));
            if (error.Data.Contains("nativeRawResult"))
                result["rawResult"] = error.Data["nativeRawResult"];
            return result;
        }

        string RetainRelationMember(UiaElement item, NativeNodeRecord source)
        {
            try
            {
                if (item == null)
                    throw new NativeFieldUnavailable("error", "UIA relation contains an unavailable object");
                return store.KeepRelated(item, source, request);
            }
            catch (Exception error)
            {
                if (!error.Data.Contains("nativeRawResult"))
                    error.Data["nativeRawResult"] = item;
                throw;
            }
        }

        void Related(UiaElement item, NativeNodeRecord source, List<string> targets, List<object> failures,
                     ref string state, ref string detail)
        {
            try
            {
                if (item == null)
                    throw new NativeFieldUnavailable("error", "UIA relation contains an unavailable object");
                targets.Add(RetainRelationMember(item, source));
            }
            catch (Exception error)
            {
                var status = Status(error);
                status["rawResult"] = item;
                failures.Add(Wire.Obj("index", targets.Count + failures.Count, "status", status));
                Check();
                state = "partial";
                detail = "A relation member could not be retained; other members are preserved: " +
                         NativeReadStore.Text(error.Message);
            }
        }

        object Actions(UiaElement e)
        {
            var items = new List<object>();
            try
            {
                Check();
                var supported = new HashSet<int>(Patterns(e).Select(p => p.Id));
                request.Check();
                foreach (string name in NativeUiaActions.Names(supported))
                    items.Add(Wire.Obj("name", name, "description", name));
                // This is the no-argument action catalogue, not a claim that
                // parameterized Value/Scroll/Transform methods do not exist.
                return Wire.Obj("state", "complete", "items", items, "total", items.Count);
            }
            catch (Exception error)
            {
                return Wire.Obj("state", "unavailable", "items", items, "detail", NativeReadStore.Text(error.Message),
                                "nativeStatus", Status(error));
            }
        }

        Dictionary<string, object> Edge(NativeNodeRecord node, string property, int limit, string cursor = null)
        {
            Trace("relation " + node.Id + " " + property);
            double membershipStart = Wire.Now;
            if (node.Outside)
                return Wire.Obj(
                    "source", node.Id, "property", property, "targets", new string[0], "certainty", "unknown", "state",
                    "unsupported", "detail",
                    "The observed object is outside the selected window; its relations are not expanded. Select its independent window to explore it.");
            if (property == "UIA.FocusedElement" || property == "UIA.PointElement")
            {
                if (cursor != null)
                    throw new Failure(
                        "snapshot_expired",
                        "passive discovery is a fresh single-object observation and has no continuation cursor");
                return new NativeReadDiscoveryPipeline(
                           new NativeReadDiscoveryProvider(store, node, Check, () => until - Wire.Now, Trace))
                    .Run(node.Id, request, new[] { property })
                    .Relations.Single();
            }
            Page page = null;
            if (cursor != null)
            {
                if (!pages.TryGetValue(cursor, out page) || page.Surface != node.Surface || page.Node != node.Id ||
                    page.Property != property)
                    throw new Failure("snapshot_expired",
                                      "native relation continuation expired or belongs to another scope");
                if (page.Captured)
                {
                    // The old weak references cannot verify a fresh provider
                    // prefix. Deliver their original capture explicitly; do
                    // not enumerate, match by position, retain or rebind them.
                    Check();
                    store.ReadCapturedNode(node.Id, node.Surface, request);
                    return PageEdge(node, property, page.Targets, page.Offset, limit, false, page.Detail,
                                    page.MembershipObservedAt, true, page.NativeStatus, page.MemberIssues);
                }
            }

            var targets = new List<string>();
            var memberFailures = new List<object>();
            string state = "complete", detail = null;
            Dictionary<string, object> nativeStatus = null;
            try
            {
                Check();
                if (property == "RawViewChildren")
                {
                    var children = cache.Children(node.Element);
                    var collected = NativeReadChildCollection.Collect(
                        children.Items, children.FiniteSnapshot,
                        child =>
                        {
                            if (child == null)
                                throw new NativeFieldUnavailable("error",
                                                                 "UIA relation contains an unavailable object");
                            return RetainRelationMember(child, node);
                        },
                        Check,
                        error =>
                            memberFailures.Add(Wire.Obj("observedPrefixCount", targets.Count, "status", Status(error))),
                        observed: targets);
                    state = NativeReadChildRoutes.Complete(collected.Complete, children.ConflictingEmptyCache)
                                ? "complete"
                                : "partial";
                    detail = collected.Detail;
                    if (children.ConflictingEmptyCache)
                        detail =
                            "UIA cache and RawViewWalker disagreed; these are separately observed walker members, and cache completeness is unknown. " +
                            detail;
                }
                else if (property == "Text.Children")
                {
                    var children = TextReader(node).Children();
                    if (children == null)
                        throw new NativeFieldUnavailable("absent", "UIA returned no embedded-object array");
                    foreach (var child in children)
                    {
                        Check();
                        if (targets.Count >= 4096)
                        {
                            state = "partial";
                            detail = "Embedded-object limit reached.";
                            break;
                        }

                        Related(child, node, targets, memberFailures, ref state, ref detail);
                    }
                }
                else if (property == "ItemContainer.Items")
                {
                    Check();
                    object raw;
                    if (!node.Element.TryGetCurrentPattern(ItemContainerPattern.Pattern, out raw))
                        throw new NativeFieldUnavailable("unsupported");
                    var pattern = (UIA.IUIAutomationItemContainerPattern)raw;
                    UiaElement previous = null;
                    var seen = new HashSet<string>();
                    while (true)
                    {
                        Check();
                        var item = UiaClient.Wrap(
                            pattern.FindItemByProperty(previous == null ? null : previous.Native, 0, null));
                        Check();
                        if (item == null)
                            break;
                        if (targets.Count >= 4096)
                        {
                            state = "partial";
                            detail =
                                "Logical item enumeration limited to 4096 items; remaining count unknown. Read the visible subtree after an explicit scroll to explore further.";
                            break;
                        }

                        string id = store.KeepRelated(item, node, request);
                        if (!seen.Add(id))
                        {
                            state = "partial";
                            detail = "ItemContainer repeated an object; enumeration stopped.";
                            break;
                        }

                        targets.Add(id);
                        previous = item;
                    }
                }
                else if (property == "Grid.Cells")
                {
                    Check();
                    object raw;
                    if (!node.Element.TryGetCurrentPattern(GridPattern.Pattern, out raw))
                        throw new NativeFieldUnavailable("unsupported");
                    var pattern = (UIA.IUIAutomationGridPattern)raw;
                    int rows = pattern.CurrentRowCount, columns = pattern.CurrentColumnCount;
                    Check();
                    long count = (long)rows * columns;
                    if (rows < 0 || columns < 0)
                        throw new NativeFieldUnavailable("error", "UIA Grid reported negative dimensions");
                    var collected = NativeReadMemberCollection.CollectPositions(
                        (int)Math.Min(count, 4096),
                        i =>
                        {
                            Check();
                            var cell = UiaClient.Wrap(pattern.GetItem(i / columns, i % columns));
                            Check();
                            if (cell == null)
                                throw new NativeFieldUnavailable("error", "Grid.GetItem returned no cell");
                            return RetainRelationMember(cell, node);
                        },
                        Check,
                        error =>
                            memberFailures.Add(Wire.Obj("observedPrefixCount", targets.Count, "status", Status(error))),
                        targets);
                    if (!collected.Complete)
                    {
                        state = "partial";
                        detail = collected.Detail;
                    }

                    int afterRows = pattern.CurrentRowCount, afterColumns = pattern.CurrentColumnCount;
                    Check();
                    if (afterRows != rows || afterColumns != columns)
                    {
                        state = "partial";
                        detail = (detail ?? "") + " Grid dimensions changed during observation.";
                    }
                    else if (count > 4096)
                    {
                        state = "partial";
                        detail = (detail ?? "") + " Grid logical coverage limited to its first 4096 positions; " +
                                 count + " positions declared. No scrolling or realization performed.";
                    }
                }
                else if (property == "RawViewParent" || property == "LabeledBy")
                {
                    UiaElement related = property == "RawViewParent"
                                             ? UiaClient.Parent(node.Element)
                                             : Get(node.Element, AutomationElement.LabeledByProperty) as UiaElement;
                    if (related != null)
                    {
                        // Parent is an observed reverse edge, not a request to
                        // traverse ancestors or an input-authority check.
                        // Labels are followed, so retain their scope proof.
                        if (property == "RawViewParent")
                            Related(related, node, targets, memberFailures, ref state, ref detail);
                        else
                            Related(related, node, targets, memberFailures, ref state, ref detail);
                    }
                }
                else if (NativeReadSchema.TableRelations.ContainsKey(property) ||
                         NativeReadSchema.ReadingRelations.ContainsKey(property))
                {
                    RequirePattern(node.Element, property);
                    object value = NativeReadSchema.TableRelations.ContainsKey(property)
                                       ? Get(node.Element, NativeReadSchema.TableRelations[property])
                                       : Get(node.Element, NativeReadSchema.ReadingRelations[property]);
                    var single = value as UiaElement;
                    var related = value as UiaElement[];
                    bool owner =
                        property == "GridItem.ContainingGrid" || property == "SelectionItem.SelectionContainer";
                    if (owner)
                    {
                        if (value != null && single == null)
                            throw new NativeFieldUnavailable("error", "UIA owner relation has an invalid type", value);
                        related = single == null ? new UiaElement[0] : new[] { single };
                    }
                    else if (related == null)
                        throw new NativeFieldUnavailable("error", "UIA member relation has an invalid type", value);
                    foreach (var item in related)
                    {
                        Check();
                        if (targets.Count >= 4096)
                        {
                            state = "partial";
                            detail = "Native member enumeration limited to 4096 objects.";
                            break;
                        }

                        // Preserve the provider's ordered membership, even if
                        // an object repeats. Node traversal has its own set.
                        Related(item, node, targets, memberFailures, ref state, ref detail);
                    }
                }
                else
                    throw new NativeFieldUnavailable("unsupported", "unknown UIA relation");
            }
            catch (Exception error)
            {
                var status = Status(error);
                string statusName = (string)status["state"];
                nativeStatus = status;
                state = targets.Count > 0 ? "partial" : statusName;
                detail = (string)status["detail"];
            }

            // A cursor remembers the observed prefix, not authority to return
            // old children as a fresh fact. Re-enumerate and require the same
            // prefix and count; an unread suffix may contain newer live data.
            if (detail != null)
                detail = NativeReadStore.Text(detail);
            if (page != null && !NativeReadRelationPaging.CanContinue(page.Targets, page.Offset, page.Complete, targets,
                                                                      state == "complete"))
                throw new Failure("snapshot_expired",
                                  "native relation changed or could not be verified since the observed page");
            // Even an incomplete enumeration may already have more observed
            // references than this page can carry. Preserve that observed tail
            // with a cursor; total counts retained members, not logical items.
            if (state == "complete" || targets.Count > 0)
                return PageEdge(node, property, targets.ToArray(), page == null ? 0 : page.Offset, limit,
                                state == "complete", detail, Stamp(membershipStart), nativeStatus: nativeStatus,
                                memberIssues: memberFailures.ToArray());
            var result = Wire.Obj("source", node.Id, "property", property, "targets", targets.Take(limit).ToArray(),
                                  "certainty", targets.Count == 0 ? "unknown" : "confirmed", "state", state, "detail",
                                  detail ?? "Native relation incomplete");
            if (nativeStatus != null)
                result["nativeStatus"] = nativeStatus;
            if (memberFailures.Count != 0)
                result["memberIssues"] = memberFailures;
            return result;
        }
        Dictionary<string, object> PageEdge(NativeNodeRecord node, string property, string[] targets, int offset,
                                            int limit, bool complete, string detail,
                                            Dictionary<string, object> membershipObservedAt, bool captured = false,
                                            Dictionary<string, object> nativeStatus = null,
                                            object[] memberIssues = null)
        {
            var shown = targets.Skip(offset).Take(limit).ToArray();
            bool weakShown = false;
            foreach (string id in shown)
            {
                var target = captured ? store.ReadCapturedNode(id, node.Surface, request)
                                      : store.ReadNode(id, node.Surface, request);
                weakShown |= NativeReadRelationPaging.NeedsCapturedMembership(target);
            }
            int end = offset + shown.Length;
            var result = Wire.Obj("source", node.Id, "property", property, "targets", shown, "certainty", "confirmed",
                                  "state", complete && end == targets.Length && offset == 0 ? "complete" : "partial",
                                  "total", targets.Length);
            if (nativeStatus != null)
                result["nativeStatus"] = nativeStatus;
            if (memberIssues != null && memberIssues.Length != 0)
                result["memberIssues"] = memberIssues;
            if (!complete)
                result["detail"] = String.IsNullOrWhiteSpace(detail)
                                       ? "Native relation incomplete; total counts only the observed members."
                                       : NativeReadStore.Text(detail + " Total counts only the observed members.");
            bool capturedNext = captured || weakShown;
            if (captured || capturedNext && end < targets.Length)
            {
                result["membershipObservedAt"] = membershipObservedAt;
                result["detail"] = NativeReadStore.Text(
                    (detail ?? "") + " Membership uses the original captured member references sampled at " +
                    membershipObservedAt["completedAt"] +
                    "; continuation is not a fresh provider membership enumeration. Original references are never rebound or granted input authority.");
            }
            if (offset != 0)
                result["offset"] = offset;
            if (end < targets.Length)
            {
                string cursor = NativeReadStore.Token("relation");
                pages[cursor] = new Page { Surface = node.Surface,
                                           Node = node.Id,
                                           Property = property,
                                           Targets = targets,
                                           Offset = end,
                                           Complete = complete,
                                           Captured = capturedNext,
                                           MembershipObservedAt = membershipObservedAt,
                                           NativeStatus = nativeStatus,
                                           MemberIssues = memberIssues,
                                           Detail = detail };
                pageOrder.Enqueue(cursor);
                while (pageOrder.Count > 64)
                    pages.Remove(pageOrder.Dequeue());
                result["cursor"] = cursor;
            }

            return result;
        }

        sealed class NodeRead
        {
            internal NativeNodeRecord Node;
            internal int Depth;
            internal Dictionary<string, object> Values, Statuses, Result;
            internal bool EarlyRead, TextTimedOut;
        }

        internal static bool PrimaryTextCandidate(Dictionary<string, object> values, int depth)
        {
            return values.ContainsKey("IsOffscreen") && Equals(values["IsOffscreen"], false) &&
                   (depth == 0 || values.ContainsKey("HasKeyboardFocus") && Equals(values["HasKeyboardFocus"], true) ||
                    values.ContainsKey("ControlType") && Equals(values["ControlType"], "ControlType.Document"));
        }

        void ObserveForegroundFocus(NativeWindowRecord selected)
        {
            // GetFocusedElement is a passive observation, not SetFocus. Some
            // providers expose only a placeholder fragment until this normal
            // UIA observation initializes their focused document. Perform it
            // before caching children, only for the selected foreground HWND.
            var window = selected.Native;
            if (Native.GetForegroundWindow() != window.Handle)
                return;
            Check();
            window.Validate();
            int milliseconds = (int)Math.Min(150, until - Wire.Now);
            if (milliseconds < UiaClient.MinimumTransactionMs)
                return;
            try
            {
                using (UiaClient.TransactionBudget(milliseconds))
                {
                    Trace("GetFocusedElement before window collection");
                    var focused = UiaElement.FocusedElement;
                    Check();
                    if (focused == null)
                        NativeReadStore.Note(observationNotes,
                                             "UIA reported no focused element; focus readiness is not established.");
                }
            }
            catch (Exception error)
            {
                request.Check();
                var status = Status(error);
                NativeReadStore.Note(observationNotes,
                                     "Passive UIA focus observation was unavailable (" + status["state"] +
                                         "); continuing the selected window read without retrying that probe.");
            }

            // The callback can race a window/focus change. Do not retain its
            // object, give it input authority, or substitute it for this App.
            window.Validate();
            if (Native.GetForegroundWindow() != window.Handle)
                NativeReadStore.Note(
                    observationNotes,
                    "Foreground changed during passive UIA focus observation; no focused object was retained.");
        }

        void ReadFields(NodeRead row, IEnumerable<string> fields, NativeCapturedText.Budget budget)
        {
            foreach (string field in fields)
            {
                try
                {
                    if (primaryFields.Contains(field) && !PrimaryTextCandidate(row.Values, row.Depth))
                    {
                        row.Statuses[field] = Wire.Obj(
                            "state", "notProbed", "detail",
                            "Default line hints probe the visible requested root, documents or focused objects only; read an observed Element to inspect another text object.");
                        continue;
                    }

                    object value = Field(row.Node, field, budget.RequestLimit);
                    var caret = field == "Text.CaretRange" ? value as Dictionary<string, object> : null;
                    if (caret != null && caret.ContainsKey("state") && Equals(caret["state"], "notProbed"))
                    {
                        row.Statuses[field] = caret;
                        continue;
                    }

                    var sample = value as NativeCapturedText.Sample;
                    var text = sample == null ? value as string : sample.Text;
                    if (text != null)
                        texts.Observe(field, text, sample == null || sample.Complete, budget, row.Values, row.Statuses);
                    else
                        row.Values[field] = value;
                    if (field == "Win32.ClientRectangle")
                        RecordNative(row.Node.Id, field, Geometry(row.Values[field], "Win32.RECT"));
                    TextSourceResolution resolution;
                    if (field.StartsWith("Text.", StringComparison.Ordinal) &&
                        textReaders.TryGetValue(row.Node.Id, out resolution) && resolution.Reader != null)
                    {
                        var reader = resolution.Reader;
                        var references = RangeReferences(reader, field);
                        var metadata = new List<object>();
                        if (references != null)
                            metadata.Add(NativeObservationGraph.Pair("native_range", references));
                        object raw;
                        if (reader.NativeValues.TryGetValue(field, out raw))
                        {
                            var original = raw as NativeObservationValue;
                            if (original != null)
                                metadata.AddRange(original.Metadata);
                            var annotation =
                                new NativeObservationValue(original == null ? raw.GetType().FullName : original.Type,
                                                           original == null ? raw : original.Data,
                                                           metadata.Select(item => (object[])item).ToArray());
                            annotation.Api = reader.Source;
                            RecordNative(row.Node.Id, field, annotation);
                        }
                        else
                        {
                            var annotation = new NativeObservationValue(
                                row.Values[field] is string ? "System.String" : "UIA.TextObservation",
                                row.Values[field], metadata.Select(item => (object[])item).ToArray());
                            annotation.Api = reader.Source;
                            RecordNative(row.Node.Id, field, annotation);
                        }
                    }
                }
                catch (Exception error)
                {
                    var status = Status(error);
                    TextSourceResolution resolution;
                    if (field.StartsWith("Text.", StringComparison.Ordinal) &&
                        textReaders.TryGetValue(row.Node.Id, out resolution) && resolution.Reader != null)
                    {
                        var reader = resolution.Reader;
                        status["nativeReadSource"] = reader.Source;
                        var references = RangeReferences(reader, field);
                        if (references != null)
                            status["nativeRanges"] = references;
                    }

                    row.Statuses[field] = status;
                }
            }
        }

        object Nodes(Dictionary<string, object> target, Dictionary<string, object> query)
        {
            double start = Wire.Now;
            nativeValues.Clear();
            rangeNodes.Clear();
            var fields = Wire.Array(query["fields"]).Cast<string>().ToArray();
            int maximum = (int)Wire.Num(query, "maxNodes", 1, 2000, true),
                depthLimit = (int)Wire.Num(query, "maxDepth", 0, 2000, true);
            var root = store.ReadNode((string)target["node"], (string)target["surface"], request);
            var followed = query.ContainsKey("followRelations")
                               ? Wire.Array(query["followRelations"]).Cast<string>().ToArray()
                               : new string[0];
            var discoveryNames =
                followed.Where(property => property == "UIA.FocusedElement" || property == "UIA.PointElement")
                    .ToArray();
            var discovered = new Dictionary<string, Dictionary<string, object>>();
            if (discoveryNames.Length != 0)
            {
                var discovery = new NativeReadDiscoveryPipeline(
                                    new NativeReadDiscoveryProvider(store, root, Check, () => until - Wire.Now, Trace))
                                    .Run(root.Id, request, discoveryNames);
                foreach (var relation in discovery.Relations)
                    discovered[(string)relation["property"]] = relation;
            }
            else
                ObserveForegroundFocus(root.Window);
            string changeToken = events.Observe(root.Window);
            var rootPage = query.ContainsKey("relationPage")   ? Wire.Object(query["relationPage"])
                           : query.ContainsKey("childrenPage") ? Wire.Object(query["childrenPage"])
                                                               : null;
            string pageProperty = rootPage != null && rootPage.ContainsKey("property") ? Wire.Str(rootPage, "property")
                                                                                       : "RawViewChildren";
            // Range records reference the same embedded objects as this edge,
            // including an explicit field-only request. Declare every identity.
            var properties =
                Relations.Concat(followed)
                    .Concat(fields.Contains("Text.ObjectRanges") ? new[] { "Text.Children" } : new string[0])
                    .Distinct()
                    .ToArray();
            focusedFields.Clear();
            if (query.ContainsKey("focusedFields"))
                foreach (string field in Wire.Array(query["focusedFields"]))
                    focusedFields.Add(field);
            primaryFields.Clear();
            if (query.ContainsKey("primaryFields"))
                foreach (string field in Wire.Array(query["primaryFields"]))
                    primaryFields.Add(field);
            cache = new NativeReadCache(fields, Check, message => NativeReadStore.Note(observationNotes, message),
                                        RecordCachedFailure);
            if (rootPage != null)
                properties = new[] { pageProperty }.Concat(properties.Where(p => p != pageProperty)).ToArray();
            if (maximum > 16384 / properties.Length)
            {
                maximum = 16384 / properties.Length;
                NativeReadStore.Note(
                    observationNotes,
                    "Node budget reduced to keep every requested relation status inside the wire graph budget.");
            }

            var pending = new Queue<Tuple<string, int>>();
            pending.Enqueue(Tuple.Create(root.Id, 0));
            var deferred = new List<NodeRead>();
            var ordinaryFields = fields.Where(f => !f.StartsWith("Text.", StringComparison.Ordinal)).ToArray();
            var textFields = fields.Except(ordinaryFields).ToArray();
            var ordinaryRelations = properties.Where(p => p != "Text.Children").ToArray();
            var textRelations = properties.Except(ordinaryRelations).ToArray();
            var textBudget = texts.BeginBatch();
            var seen = new HashSet<string>();
            var referenced = new HashSet<string>();
            var nodes = new List<object>();
            var edges = new List<object>();
            var notes = observationNotes;
            int edgeBudget = 8000, wireBytes = 0;
            Action<NodeRead, string[]> readEdges = (row, names) =>
            {
                foreach (string property in names)
                {
                    Dictionary<string, object> edge;
                    Dictionary<string, object> discovery;
                    if (edgeBudget == 0)
                        edge =
                            Wire.Obj("source", row.Node.Id, "property", property, "targets", new string[0], "certainty",
                                     "unknown", "state", "partial", "detail", "Fact graph relation budget exhausted");
                    else if (discovered.TryGetValue(property, out discovery))
                        edge =
                            row.Node.Id == root.Id
                                ? discovery
                                : Wire.Obj(
                                      "source", row.Node.Id, "property", property, "targets", new string[0],
                                      "certainty", "unknown", "state", "unsupported", "detail",
                                      "Passive window discovery is collected once at the requested root; it does not expand this descendant's scope.");
                    else
                    {
                        var page = row.Node.Id == root.Id && property == pageProperty ? rootPage : null;
                        edge =
                            Edge(row.Node, property,
                                 Math.Min(page == null ? 128 : (int)Wire.Num(page, "limit", 1, 1999, true), edgeBudget),
                                 page == null ? null : page["cursor"] as string);
                    }

                    var children = Wire.Array(edge["targets"]).Cast<string>().ToArray();
                    edgeBudget -= children.Length;
                    edges.Add(edge);
                    wireBytes += Encoding.UTF8.GetByteCount(Wire.Json(edge));
                    foreach (string child in children)
                    {
                        referenced.Add(child);
                        bool follow = property == "RawViewChildren" || property == "LabeledBy" ||
                                      followed.Contains(property) ||
                                      rootPage != null && row.Node.Id == root.Id && property == pageProperty;
                        if (follow && row.Depth < depthLimit)
                            pending.Enqueue(Tuple.Create(child, row.Depth + 1));
                    }
                }
            };
            // Keep a structural batch ahead of enrichment. Only confirmed
            // visible documents/focus can use an early primary-text slot;
            // hidden diagnostic providers wait until structure is collected.
            // Other fields/branches retain their FIFO turn and exact coverage.
            int structureBatch = 0, priorityBurst = 0;
            double earlyTime = 0, batchStarted = Wire.Now;
            Func<NodeRead, bool> primaryCandidate = row =>
                row.Values.ContainsKey("IsOffscreen") && Equals(row.Values["IsOffscreen"], false) &&
                (row.Values.ContainsKey("HasKeyboardFocus") && Equals(row.Values["HasKeyboardFocus"], true) ||
                 row.Values.ContainsKey("ControlType") && Equals(row.Values["ControlType"], "ControlType.Document"));
            var primaryText = textFields.Where(f => f == "Text.DocumentRange" || f == "Text.Source").ToArray();
            Action<NodeRead, string[], bool> enrich = (row, names, relations) =>
            {
                int before = Encoding.UTF8.GetByteCount(Wire.Json(row.Result));
                double saved = until;
                until = Math.Min(saved, Wire.Now + 350);
                try
                {
                    int transactionMs = (int)(until - Wire.Now);
                    if (transactionMs < UiaClient.MinimumTransactionMs)
                        row.TextTimedOut = true;
                    if (!row.TextTimedOut)
                        using (UiaClient.TransactionBudget(transactionMs))
                        {
                            ReadFields(row,
                                       names.Where(f => !row.Values.ContainsKey(f) && !row.Statuses.ContainsKey(f)),
                                       textBudget);
                            row.TextTimedOut = names.Any(f => row.Statuses.ContainsKey(f) &&
                                                              Equals(Wire.Object(row.Statuses[f])["state"], "timeout"));
                            if (relations && !row.TextTimedOut)
                                readEdges(row, textRelations);
                        }

                    if (row.TextTimedOut)
                    {
                        const string detail =
                            "The text collection slice was exhausted or too short for a supported UIA transaction; remaining fields were not retried in this observation.";
                        foreach (string field in names.Where(f => !row.Values.ContainsKey(f) &&
                                                                  !row.Statuses.ContainsKey(f)))
                            row.Statuses[field] = Wire.Obj("state", "timeout", "detail", detail);
                        if (relations)
                            foreach (string property in textRelations)
                                edges.Add(Wire.Obj("source", row.Node.Id, "property", property, "targets",
                                                   new string[0], "certainty", "unknown", "state", "timeout", "detail",
                                                   detail));
                        NativeReadStore.Note(notes, detail);
                    }
                }
                finally
                {
                    until = saved;
                }

                wireBytes += Encoding.UTF8.GetByteCount(Wire.Json(row.Result)) - before;
            };
            while (pending.Count > 0 && nodes.Count < maximum || deferred.Count > 0)
            {
                if (wireBytes >= 8 * 1024 * 1024 || Wire.Now >= until)
                    break;
                if (pending.Count == 0 || nodes.Count >= maximum)
                {
                    var preferred = deferred.FirstOrDefault(primaryCandidate);
                    var row = preferred != null && priorityBurst < 2 ? preferred : deferred[0];
                    deferred.Remove(row);
                    priorityBurst = primaryCandidate(row) ? priorityBurst + 1 : 0;
                    enrich(row, textFields, true);
                    continue;
                }

                if (primaryText.Length != 0 && earlyTime < 600 && until - Wire.Now > 500 &&
                    (structureBatch >= 32 || Wire.Now - batchStarted >= 150))
                {
                    var candidate = deferred.Where(row => !row.EarlyRead && !row.TextTimedOut && primaryCandidate(row))
                                        .OrderBy(row => row.Values.ContainsKey("HasKeyboardFocus") &&
                                                                Equals(row.Values["HasKeyboardFocus"], true)
                                                            ? 0
                                                            : 1)
                                        .FirstOrDefault();
                    if (candidate != null)
                    {
                        candidate.EarlyRead = true;
                        double began = Wire.Now;
                        enrich(candidate, primaryText, false);
                        earlyTime += Wire.Now - began;
                        structureBatch = 0;
                        batchStarted = Wire.Now;
                        continue;
                    }
                }

                request.Check();
                var item = pending.Dequeue();
                if (seen.Contains(item.Item1))
                    continue;
                NativeNodeRecord node;
                try
                {
                    node = store.ReadNode(item.Item1, root.Surface, request);
                }
                catch (Exception error)
                {
                    if (item.Item1 == root.Id)
                        throw;
                    Status(error);
                    NativeReadStore.Note(
                        notes, "A related object became unavailable during collection; its reference remains unread.");
                    continue;
                }

                seen.Add(item.Item1);
                structureBatch++;
                var values = new Dictionary<string, object>();
                var statuses = new Dictionary<string, object>();
                var rowRead = new NodeRead { Node = node, Depth = item.Item2, Values = values, Statuses = statuses };
                ReadFields(rowRead, ordinaryFields, textBudget);
                rowRead.Result = Wire.Obj("node", node.Id, "requestedFields", fields, "values", values, "fieldStatus",
                                          statuses, "actions", Actions(node.Element));
                nodes.Add(rowRead.Result);
                wireBytes += Encoding.UTF8.GetByteCount(Wire.Json(rowRead.Result));
                readEdges(rowRead, ordinaryRelations);
                if (textFields.Length != 0 || textRelations.Length != 0)
                    deferred.Add(rowRead);
            }

            if (nodes.Count == 0)
                throw new Failure(
                    "observation_unavailable",
                    "the shared preparation and collection budget expired before a root snapshot could be collected",
                    "read the same restored target again; no content input was replayed");
            if (wireBytes >= 8 * 1024 * 1024 || Wire.Now >= until)
            {
                string state = Wire.Now >= until ? "timeout" : "partial";
                string detail =
                    state == "timeout"
                        ? "Bounded UIA collection time exhausted; remaining fields and relations are unread."
                        : "Native collection wire budget exhausted; remaining fields and relations are unread.";
                NativeReadStore.Note(notes, detail);
                foreach (var row in deferred)
                {
                    foreach (string field in textFields.Where(f => !row.Values.ContainsKey(f) &&
                                                                   !row.Statuses.ContainsKey(f)))
                        row.Statuses[field] = Wire.Obj("state", state == "partial" ? "error" : state, "detail", detail);
                    foreach (string property in textRelations)
                        edges.Add(Wire.Obj("source", row.Node.Id, "property", property, "targets", new string[0],
                                           "certainty", "unknown", "state", "partial", "detail", detail));
                }
            }

            if (pending.Count > 0 || referenced.Except(seen).Any())
                NativeReadStore.Note(notes,
                                     "Some related nodes remain unread due to collection scope, depth or node budget.");
            return NativeObservationGraph.Encode(
                session, root.Surface, Stamp(start), new[] { root.Id }, nodes, edges, referenced.Except(seen), notes,
                properties, changeToken, nativeValues, rangeNodes.Values,
                id =>
                {
                    var identity = store.ObservedRuntime(id);
                    return identity == null ? null : new NativeObservationValue("UIA.RuntimeId", identity);
                },
                RetainOpaque, cachedFailures);
        }

        object Directory(Dictionary<string, object> query)
        {
            double start = Wire.Now;
            var notes = new List<string>();
            bool accessibility =
                !NeedsRestart && (!query.ContainsKey("includeAccessibility") || (bool)query["includeAccessibility"]);
            var all = store.Catalog(request, notes, accessibility);
            string kind = Wire.Str(query, "kind");
            if (kind == "applications")
            {
                return Wire.Obj(
                    "kind", kind, "session", session, "platform", "windows", "observed", Stamp(start), "stopReasons",
                    notes, "scope", "runningWindows", "applications",
                    all.GroupBy(x => x.Application)
                        .Select(
                            g => Wire.Obj("id", g.Key, "displayName", NativeReadStore.Text(g.First().Native.Name),
                                          "isRunning", true, "path", null, "identifier", null, "windows",
                                          g.Select(w => Wire.Obj("selector", w.Native.Selector, "window", w.Native.Ref))
                                              .ToArray()))
                        .ToArray());
            }

            string application = Wire.Str(query, "application");
            var selected =
                new HashSet<IntPtr>(all.Where(x => x.Application == application).Select(x => x.Native.Handle));
            if (selected.Count == 0)
                throw new Failure("stale_target", "the observed running application no longer has an available window");
            for (int n = 0; n < all.Count; n++)
            {
                var children = all.Where(x => !selected.Contains(x.Native.Handle) &&
                                              selected.Contains(Native.GetWindow(x.Native.Handle, 4)))
                                   .ToArray();
                if (children.Length == 0)
                    break;
                foreach (var child in children)
                    selected.Add(child.Native.Handle);
            }

            var entries = all.Where(x => selected.Contains(x.Native.Handle)).ToArray();
            var roots = accessibility && !NeedsRestart ? rootProbe.Read(entries, request) : null;
            if (NeedsRestart)
            {
                NativeReadStore.Note(
                    notes,
                    "UIA window enrichment timed out; Win32 window identities are preserved. The blocked read worker is isolated and replaced; UIA roots and modality are unknown.");
            }

            Trace("directory surfaces collected");
            var rows = new List<object>();
            foreach (var entry in entries)
            {
                request.Check();
                // Enumeration and enrichment are not atomic. A tooltip or
                // another window may close without ending the working window.
                // Its stale identity must not invalidate every other row.
                try
                {
                    entry.Native.Validate();
                }
                catch (Failure error)
                {
                    if (error.Code != "stale_target")
                        throw;
                    NativeReadStore.Note(
                        notes, "A window closed or was replaced during directory collection; its entry was omitted.");
                    continue;
                }

                string node = accessibility || NeedsRestart ? null : entry.RootNode;
                NativeRootProbe.Root root = null;
                if (roots != null)
                    roots.TryGetValue(entry.Native.Handle, out root);
                if (root != null)
                {
                    if (root.Error != null)
                        NativeReadStore.Note(notes, "Window UIA unavailable: " + root.Error);
                    try
                    {
                        Check();
                        if (root.Element != null)
                            node = store.Keep(root.Element, entry, request);
                    }
                    catch (Exception error)
                    {
                        Status(error);
                        NativeReadStore.Note(notes, "Window UIA unavailable: " + NativeReadStore.Text(error.Message));
                    }
                }

                if (accessibility)
                    entry.RootNode = node;
                IntPtr owner = Native.GetWindow(entry.Native.Handle, 4);
                var parent = all.FirstOrDefault(x => x.Native.Handle == owner);
                object modal = NeedsRestart    ? null
                               : accessibility ? (root == null ? null : root.Modal)
                                               : entry.ObservedModal;
                string title = Native.Title(entry.Native.Handle);
                if (accessibility && modal is bool)
                    entry.ObservedModal = (bool)modal;
                if (title.Length >= 4095)
                    NativeReadStore.Note(
                        notes, "A native window title reached the 4096-character Win32 buffer and may be truncated.");
                rows.Add(Wire.Obj("window", entry.Native.Ref, "instance", entry.Native.Selector, "surface",
                                  entry.Native.Scope, "node", node, "title", NativeReadStore.Text(title), "kind",
                                  "window", "parent", parent == null ? null : parent.Native.Ref, "parentState",
                                  parent != null         ? "known"
                                  : owner == IntPtr.Zero ? "none"
                                                         : "unknown",
                                  "modal", modal, "nativeWindow", true, "accessibility", node != null));
            }

            return Wire.Obj("kind", kind, "session", session, "platform", "windows", "observed", Stamp(start),
                            "stopReasons", notes, "application", application, "provider",
                            Wire.Obj("status",
                                     NeedsRestart     ? "unavailable"
                                     : !accessibility ? "notRequested"
                                     : rows.Cast<Dictionary<string, object>>().Any(x => (bool)x["accessibility"])
                                         ? "available"
                                         : "unavailable",
                                     "attribute", null, "writeStatus", null),
                            "surfaces", rows);
        }

        public object Inspect(Dictionary<string, object> target, Dictionary<string, object> query, Request r)
        {
            request = r;
            until = Math.Min(r.ObservationDeadline, Math.Min(r.Deadline, Wire.Now + 2500));
            string kind = Wire.Str(query, "kind");
            if (kind == "nodes" && Wire.Now >= until)
                throw new Failure("observation_unavailable",
                                  "foreground preparation used the shared observation budget before root collection",
                                  "read the same restored target again; no content input was replayed");
            observationNotes = new List<string>(preparationNotes);
            textReaders.Clear();
            cachedFailures.Clear();
            rangeNodes.Clear();
            cache = new NativeReadCache(
                new string[0], Check, message => NativeReadStore.Note(observationNotes, message), RecordCachedFailure);
            if (kind == "applications" || kind == "surfaces")
                return Directory(query);
            if (kind == "nodes")
                return Nodes(target, query);
            if (kind == "readChange")
                return events.Wait(session, store.Validate(target, r, true), Wire.Str(query, "after"),
                                   (int)Wire.Num(query, "timeoutMs", 1, 400, true), r);
            if (kind == "capturedText")
                return Wire.Obj("kind", kind, "session", session, "textRef", Wire.Str(query, "textRef"), "page",
                                texts.Page(Wire.Str(query, "textRef"),
                                           (long)Wire.Num(query, "offset", 0, 9007199254740991, true),
                                           (int)Wire.Num(query, "length", 2, NativeCapturedText.PreviewUnits, true)));
            double start = Wire.Now;
            var node = store.ReadNode((string)target["node"], (string)target["surface"], request);
            var edge = Edge(node, Wire.Str(query, "property"), (int)Wire.Num(query, "limit", 1, 2000, true),
                            query["cursor"] as string);
            return NativeObservationGraph.Encode(
            session, node.Surface, Stamp(start), new[] { node.Id }, new object[0], new[] { edge },
            Wire.Array(edge["targets"]).Cast<string>(), observationNotes, new[] { (string)edge["property"] },
            resources: rangeNodes.Values, identity: id => {
                var identity = store.ObservedRuntime(id);
                return identity == null ? null : new NativeObservationValue("UIA.RuntimeId", identity);
            }, opaque: RetainOpaque, cachedFailures: cachedFailures);
        }
    }
}
