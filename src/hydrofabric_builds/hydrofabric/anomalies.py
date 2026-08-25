"""Anomaly handlers for specific flowpath IDs that need special treatment during tracing."""

from __future__ import annotations

from collections.abc import Callable

from hydrofabric_builds.hydrofabric.trace import (
    Context,
    State,
    aggregate,
    enqueue,
    mark_virtual_tree,
    traverse_and_aggregate,
)


def _anomaly_9272756(ctx: Context, st: State, curr_id: str, ds_id: str) -> None:
    st.aggregation_set.add(curr_id)
    mark_virtual_tree(ctx, st, "9272732", curr_id)
    st.aggregation_set.add("9272732")
    aggregate(st, curr_id, "9272706")
    mark_virtual_tree(ctx, st, "9272688", curr_id)
    aggregate(st, "9272706", "9272686")
    aggregate(st, "9272686", "9270812")
    aggregate(st, "9270812", "9272318")
    st.independent.discard(ds_id)
    enqueue(st, ["9270644", "9272308"])


def _anomaly_7262417(ctx: Context, st: State, curr_id: str, ds_id: str) -> None:
    traverse_and_aggregate(ctx, st, "7262465")
    st.independent.add(curr_id)
    enqueue(st, ["7262413"])


def _anomaly_7262801(ctx: Context, st: State, curr_id: str, ds_id: str) -> None:
    aggregate(st, "7262727", curr_id)
    st.non_nextgen.add("7262683")
    if "7262683" not in st.non_nextgen_virtual_sources:
        st.non_nextgen_virtual_sources.add("7262683")
        st.non_nextgen_virtual_pairs.append(("7262683", "7262727"))
    aggregate(st, "7262727", "7262683")
    st.aggregation_set.update(["7262683", "7262727", curr_id])

    st.non_nextgen.add("7262819")
    aggregate(st, "7262819", "7262727")
    st.aggregation_set.add("7262819")
    if "7262819" not in st.non_nextgen_virtual_sources:
        st.non_nextgen_virtual_sources.add("7262819")
        st.non_nextgen_virtual_pairs.append(("7262819", "7262727"))

    for src, tgt in [("7262727", "7262805"), ("7262805", "7262887"), ("7262887", "7262959")]:
        aggregate(st, src, tgt)
        st.aggregation_set.add(tgt)

    mark_virtual_tree(ctx, st, "7262803", "7262805")
    mark_virtual_tree(ctx, st, "7262933", "7262887")
    enqueue(st, ["940200288"])


def _anomaly_7261789(ctx: Context, st: State, curr_id: str, ds_id: str) -> None:
    mark_virtual_tree(ctx, st, "7261795", curr_id)

    chain = [(curr_id, "7262239"), ("7262239", "7262253"), ("7262253", "7262291")]
    for src, tgt in chain:
        aggregate(st, src, tgt)
        st.aggregation_set.add(tgt)
    st.aggregation_set.update([curr_id, ds_id])

    mark_virtual_tree(ctx, st, "7262255", "7262253")
    enqueue(st, ["7262417"])


def _anomaly_7264167(ctx: Context, st: State, curr_id: str, ds_id: str) -> None:
    aggregate(st, curr_id, "7260693")
    st.aggregation_set.update([curr_id, "7260693"])
    mark_virtual_tree(ctx, st, "7264125", curr_id)

    for src, tgt in [("7260693", "7260373"), ("7260373", "7260303")]:
        aggregate(st, src, tgt)
        st.aggregation_set.add(tgt)

    aggregate(st, "7260373", "7264103")
    st.aggregation_set.add("7264103")
    if "7264103" not in st.non_nextgen_virtual_sources:
        st.non_nextgen_virtual_sources.add("7264103")
        st.non_nextgen_virtual_pairs.append(("7264103", "7260373"))

    enqueue(st, ["7264107"])


def _anomaly_mark_virtual_tree(ctx: Context, st: State, curr_id: str, ds_id: str) -> None:
    mark_virtual_tree(ctx, st, curr_id, ds_id)
    st.independent.discard(ds_id)


def _anomaly_22769238(ctx: Context, st: State, curr_id: str, ds_id: str) -> None:
    mark_virtual_tree(ctx, st, "22769236", curr_id)
    aggregate(st, curr_id, "22769244")
    st.aggregation_set.update([curr_id, "22769244"])
    enqueue(st, ["22769244"])


def _anomaly_7257691(ctx: Context, st: State, curr_id: str, ds_id: str) -> None:
    aggregate(st, curr_id, "7258923")
    aggregate(st, "7258923", "7257829")
    st.aggregation_set.update([curr_id, "7258923", "7257829"])
    enqueue(st, ["7257829"])


def _anomaly_19058436(ctx: Context, st: State, curr_id: str, ds_id: str) -> None:
    mark_virtual_tree(ctx, st, "19058434", curr_id)
    aggregate(st, curr_id, "19058438")
    st.aggregation_set.update([curr_id, "19058438"])
    mark_virtual_tree(ctx, st, "19058412", curr_id)

    for src, tgt in [("19058438", "19058408"), ("19058408", "940180112")]:
        aggregate(st, src, tgt)
        st.aggregation_set.add(tgt)

    traverse_and_aggregate(ctx, st, "19058240")
    traverse_and_aggregate(ctx, st, "940180111")


def _anomaly_21532894(ctx: Context, st: State, curr_id: str, ds_id: str) -> None:
    aggregate(st, curr_id, "21534286")
    st.aggregation_set.update([curr_id, "21534286"])
    mark_virtual_tree(ctx, st, "21532928", curr_id)

    chain = [("21534286", "21532958"), ("21532958", "21532956"), ("21532956", "21533002")]
    trees = ["21532950", "21532954"]
    for (src, tgt), tree in zip(chain[:2], trees, strict=False):
        aggregate(st, src, tgt)
        st.aggregation_set.add(tgt)
        mark_virtual_tree(ctx, st, tree, curr_id)

    aggregate(st, "21532956", "21533002")
    st.aggregation_set.add("21533002")

    aggregate(st, "21532956", "21534452")
    st.aggregation_set.add("21534452")
    st.connectors.add("21534452")
    enqueue(st, ["21534456", "21534462", "21534454"])


def _anomaly_12745197(ctx: Context, st: State, curr_id: str, ds_id: str) -> None:
    for tgt in ["12744163", "12744931", "12745039"]:
        aggregate(st, curr_id, tgt)
        st.aggregation_set.add(tgt)
    st.aggregation_set.add(curr_id)
    enqueue(st, ["12745041"])


def _anomaly_3254269(ctx: Context, st: State, curr_id: str, ds_id: str) -> None:
    aggregate(st, curr_id, ds_id)
    st.aggregation_set.update([curr_id, ds_id])
    st.independent.discard(ds_id)
    aggregate(st, "3254167", "3258317")
    st.aggregation_set.update(["3254167", "3258317"])
    enqueue(st, ["3258317", "3254159"])


def _anomaly_7264077(ctx: Context, st: State, curr_id: str, ds_id: str) -> None:
    aggregate(st, curr_id, "7259909")
    st.aggregation_set.add(curr_id)
    mark_virtual_tree(ctx, st, "7259793", curr_id)
    traverse_and_aggregate(ctx, st, "7259795")


def _anomaly_17493533(ctx: Context, st: State, curr_id: str, ds_id: str) -> None:
    aggregate(st, curr_id, "17493529")
    st.aggregation_set.update([curr_id, "17493529"])
    mark_virtual_tree(ctx, st, "17493279", "17493533")
    aggregate(st, "17493529", "17493261")
    st.aggregation_set.add("17493261")
    enqueue(st, ["17493321", "17493245"])


def _anomaly_12327133(ctx: Context, st: State, curr_id: str, ds_id: str) -> None:
    mark_virtual_tree(ctx, st, "12327137", curr_id)
    aggregate(st, curr_id, "12327125")
    st.aggregation_set.update(["12327125", curr_id])
    enqueue(st, ["12327119"])


def _anomaly_3023064(ctx: Context, st: State, curr_id: str, ds_id: str) -> None:
    mark_virtual_tree(ctx, st, "3023066", curr_id)
    aggregate(st, curr_id, "3023062")
    st.aggregation_set.update([curr_id, "3023062"])
    aggregate(st, "3023012", "3023062")
    st.aggregation_set.add("3023012")
    aggregate(st, "3022994", "3022998")
    st.aggregation_set.update(["3022994", "3022998"])
    enqueue(st, ["3022996", "3022994"])


def _anomaly_5353277(ctx: Context, st: State, curr_id: str, ds_id: str) -> None:
    mark_virtual_tree(ctx, st, "5353283", curr_id)
    st.aggregation_set.add("5353277")
    aggregate(st, curr_id, "5353281")
    st.aggregation_set.add("5353281")
    enqueue(st, ["5352717"])


def _anomaly_traverse_only(ctx: Context, st: State, curr_id: str, ds_id: str) -> None:
    traverse_and_aggregate(ctx, st, curr_id)


def _anomaly_mark_with_agg(ctx: Context, st: State, curr_id: str, ds_id: str) -> None:
    mark_virtual_tree(ctx, st, curr_id, ds_id)
    st.aggregation_set.add(curr_id)


ANOMALY_HANDLERS: dict[str, Callable[[Context, State, str, str], None]] = {
    "9272756": _anomaly_9272756,
    "7262417": _anomaly_7262417,
    "7262801": _anomaly_7262801,
    "7261789": _anomaly_7261789,
    "7264167": _anomaly_7264167,
    "13257313": _anomaly_mark_virtual_tree,
    "4342468": _anomaly_mark_virtual_tree,
    "22769238": _anomaly_22769238,
    "7257691": _anomaly_7257691,
    "19058436": _anomaly_19058436,
    "21532894": _anomaly_21532894,
    "12745197": _anomaly_12745197,
    "3254269": _anomaly_3254269,
    "7264077": _anomaly_7264077,
    "17493533": _anomaly_17493533,
    "12327133": _anomaly_12327133,
    "3023064": _anomaly_3023064,
    "5353277": _anomaly_5353277,
    **dict.fromkeys(
        [
            "17245948",
            "4386267",
            "8367918",
            "7195127",
            "12778333",
            "12615764",
            "12444133",
            "3053522",
            "18852694",
            "7312267",
        ],
        _anomaly_traverse_only,
    ),
    "8367540": _anomaly_mark_with_agg,
    "14625948": _anomaly_mark_with_agg,
}
