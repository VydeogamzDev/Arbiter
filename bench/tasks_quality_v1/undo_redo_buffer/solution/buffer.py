class TextBuffer:
    def __init__(self, history_limit=100):
        self.text = ""
        self.limit = history_limit
        self._undo = []   # steps: [kind, pos, text, typing]
        self._redo = []

    def _push(self, step):
        self._undo.append(step)
        if len(self._undo) > self.limit:
            self._undo.pop(0)
        self._redo.clear()

    def insert(self, pos, text):
        if pos < 0 or pos > len(self.text):
            raise IndexError("position outside the text")
        self.text = self.text[:pos] + text + self.text[pos:]
        last = self._undo[-1] if self._undo else None
        typing = len(text) == 1
        if (typing and text not in " \n" and last is not None and last[0] == "ins" and last[3]
                and not self._redo and pos == last[1] + len(last[2])):
            last[2] += text
            return
        self._push(["ins", pos, text, typing])

    def delete(self, pos, length):
        if pos < 0 or length < 0 or pos + length > len(self.text):
            raise IndexError("range outside the text")
        removed = self.text[pos:pos + length]
        self.text = self.text[:pos] + self.text[pos + length:]
        self._push(["del", pos, removed, False])

    def undo(self):
        if not self._undo:
            return False
        kind, pos, text, typing = step = self._undo.pop()
        if kind == "ins":
            self.text = self.text[:pos] + self.text[pos + len(text):]
        else:
            self.text = self.text[:pos] + text + self.text[pos:]
        self._redo.append(step)
        return True

    def redo(self):
        if not self._redo:
            return False
        step = self._redo.pop()
        kind, pos, text, _ = step
        if kind == "ins":
            self.text = self.text[:pos] + text + self.text[pos:]
        else:
            self.text = self.text[:pos] + self.text[pos + len(text):]
        step[3] = False            # a redone step doesn't absorb new typing
        self._undo.append(step)
        return True
