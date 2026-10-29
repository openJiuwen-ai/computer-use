// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

using System;
using System.Collections.Generic;

namespace ComputerUse.Windows
{
    // GetFocusedElement and RawViewWalker.GetParentElement are passive native
    // queries. No SetFocus, activation, search or input preparation occurs here.
    internal sealed class NativeUiaCurrentReceiver : INativeCurrentReceiverProvider
    {
        readonly NativeReadStore store;
        readonly NativeUiaBoundary boundary;
        internal NativeUiaCurrentReceiver(NativeReadStore store)
        {
            this.store = store;
            boundary = new NativeUiaBoundary(store);
        }

        sealed class Tree : INativeCurrentReceiverTree
        {
            readonly NativeReadStore store;
            readonly NativeWindowRecord window;
            internal Tree(NativeReadStore store, NativeWindowRecord window)
            {
                this.store = store;
                this.window = window;
            }

            static T Query<T>(Request r, Func<T> query)
            {
                r.Check();
                try
                {
                    var result = query();
                    r.Check();
                    return result;
                }
                catch (Failure)
                {
                    throw;
                }
                catch (Exception error)
                {
                    r.Check();
                    throw new Failure("observation_incomplete", "UIA focus provider query failed: " + error.Message);
                }
            }

            public object Focused(Request r)
            {
                return Query(r, () => UiaElement.FocusedElement);
            }

            public object Parent(object element, Request r)
            {
                return Query(r, () => UiaClient.Parent((UiaElement)element));
            }

            public string Identity(object element, Request r)
            {
                return Query(r, () => NativeReadStore.Runtime((UiaElement)element));
            }

            public string Retain(object element, Request r)
            {
                return Query(r, () => store.Keep((UiaElement)element, window, r));
            }
        }

        public NativeCurrentReceiverEvidence Read(Dictionary<string, object> scope, Request r)
        {
            var window = boundary.Validate(scope, new string[0], r);
            var node = store.Node((string)scope["node"], (string)scope["surface"], r);
            return CurrentReceiverPath.Read(node.Element, node.Id, new Tree(store, node.Window), r, () => NativeUiaBoundary.SameWindow(window, boundary.Validate(scope, new string[0], r), r));
        }
    }
}
