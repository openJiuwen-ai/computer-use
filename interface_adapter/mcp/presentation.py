from computer_use._identity import WeakIdentityMap
from computer_use._json import dumps
from computer_use.interaction.read.delivery.baseline.model import BaselineInput
from computer_use.interaction.read.delivery.baseline.pipeline import BaselinePublicationPipeline
from computer_use.interaction.read.delivery.diff.model import DeliverySelection
from computer_use.interaction.read.delivery.diff.pipeline import DeliverySelectionPipeline
from computer_use.session.observation import peekObservationText


class ObservationPresenter:
    def __init__(self, maxBytes=4 * 1024 * 1024, maxScopes=32):
        self.maxBytes, self.maxScopes = maxBytes, maxScopes
        self._baselines, self._sources = {}, WeakIdentityMap()
        self._nextSource = self._bytes = 0

    def key(self, observation):
        id_ = self._sources.get(observation["source"])
        if id_ is None:
            self._nextSource += 1
            id_ = self._nextSource
            self._sources.set(observation["source"], id_)
        return dumps([id_, observation["scope"]])

    def invalidate(self, source=None):
        for key, baseline in list(self._baselines.items()):
            if source is None or baseline["source"] is source:
                self._bytes -= baseline["bytes"]
                self._baselines.pop(key)

    def text(self, observation):
        previous = self._baselines.get(self.key(observation))
        return DeliverySelectionPipeline().run(DeliverySelection(observation, previous))

    def delivered(self, observation):
        BaselinePublicationPipeline(self).run(BaselineInput(self.key(observation), observation))

    def retire_baseline(self, key):
        previous = self._baselines.pop(key, None)
        if previous is not None:
            self._bytes -= previous["bytes"]

    def publish_baseline(self, draft):
        if draft.size > self.maxBytes:
            return
        while self._baselines and (len(self._baselines) >= self.maxScopes or self._bytes + draft.size > self.maxBytes):
            self.retire_baseline(next(iter(self._baselines)))
        self._baselines[draft.key] = draft.baseline
        self._bytes += draft.size


class ObservationOutput:
    def __init__(self, presenter, incremental=True):
        self.presenter, self.incremental = presenter, incremental
        self._pending, self._printed = {}, set()

    def observe(self, observation):
        key = self.presenter.key(observation)
        if key not in self._pending and len(self._pending) >= 32:
            raise RuntimeError(
                "Too many observation scopes in one call; inspect the collected results before reading more."
            )
        previous = self._pending.get(key)
        if previous is not None:
            self._printed.discard(id(previous))
        self._pending[key] = observation

    def invalidate(self, source):
        self.presenter.invalidate(source)
        self.discardPending(source)

    def discardPending(self, source):
        for key, observation in list(self._pending.items()):
            if observation["source"] is source:
                self._pending.pop(key)
                self._printed.discard(id(observation))

    def printed(self, text):
        matches = [v for v in self._pending.values() if peekObservationText(v["state"]) == text]
        if len(matches) != 1:
            return None
        self.presenter.delivered(matches[0])
        self._printed.add(id(matches[0]))
        return matches[0]

    def flush(self, emit, emitImage=None):
        for observation in self._pending.values():
            if id(observation) not in self._printed:
                emit(
                    self.presenter.text(observation) if self.incremental else observation["state"]["text"], observation
                )
                self.presenter.delivered(observation)
                self._printed.add(id(observation))
            if (
                observation["view"] in ("window", "desktop")
                and observation["state"].get("screenshot") is not None
                and emitImage is not None
            ):
                emitImage(observation["state"]["screenshot"])
        self._pending.clear()
        self._printed.clear()
