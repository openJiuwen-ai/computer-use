// Real Qt text recipients for the Linux v5 typed-text acceptance.
// Build: c++ -std=c++17 -fPIC v5_typed_text_fixture.cpp -o typed-fixture \
//        $(pkg-config --cflags --libs Qt6Widgets)
// KeyboardEntry is a real QLineEdit with Component/Text accessibility, but no
// EditableText interface. Its text and selection are never synthesized by the
// fixture. Stdin only arranges owned-window focus and actual native selections.
#include <QAccessible>
#include <QAccessibleWidget>
#include <QApplication>
#include <QFile>
#include <QJsonArray>
#include <QJsonDocument>
#include <QJsonObject>
#include <QKeyEvent>
#include <QLabel>
#include <QLineEdit>
#include <QMap>
#include <QSaveFile>
#include <QSocketNotifier>
#include <QTimer>
#include <QVBoxLayout>
#include <QWidget>
#include <iostream>

class KeyboardEntry : public QLineEdit {
  public:
    using QLineEdit::QLineEdit;
};

class KeyboardAccessible : public QAccessibleWidget, public QAccessibleTextInterface {
    QLineEdit *entry() const { return static_cast<QLineEdit *>(object()); }

  public:
    explicit KeyboardAccessible(KeyboardEntry *widget) : QAccessibleWidget(widget, QAccessible::EditableText) {}
    void *interface_cast(QAccessible::InterfaceType type) override {
        if (type == QAccessible::TextInterface)
            return static_cast<QAccessibleTextInterface *>(this);
        if (type == QAccessible::EditableTextInterface)
            return nullptr;
        return QAccessibleWidget::interface_cast(type);
    }
    QAccessible::State state() const override {
        auto result = QAccessibleWidget::state();
        result.editable = !entry()->isReadOnly();
        result.readOnly = entry()->isReadOnly();
        return result;
    }
    QString text(QAccessible::Text type) const override {
        return type == QAccessible::Value ? entry()->text() : QAccessibleWidget::text(type);
    }
    void selection(int index, int *start, int *end) const override {
        *start = *end = -1;
        if (index == 0 && entry()->hasSelectedText()) {
            *start = entry()->selectionStart();
            *end = *start + int(entry()->selectedText().size());
        }
    }
    int selectionCount() const override { return entry()->hasSelectedText() ? 1 : 0; }
    void addSelection(int start, int end) override { setSelection(0, start, end); }
    void removeSelection(int index) override {
        if (index == 0)
            entry()->deselect();
    }
    void setSelection(int index, int start, int end) override {
        if (index == 0 && start >= 0 && end >= start && end <= entry()->text().size())
            entry()->setSelection(start, end - start);
    }
    int cursorPosition() const override { return entry()->cursorPosition(); }
    void setCursorPosition(int position) override { entry()->setCursorPosition(position); }
    QString text(int start, int end) const override {
        if (end < 0)
            end = int(entry()->text().size());
        return entry()->text().mid(start, end - start);
    }
    int characterCount() const override { return int(entry()->text().size()); }
    QRect characterRect(int) const override { return {}; }
    int offsetAtPoint(const QPoint &) const override { return -1; }
    void scrollToSubstring(int, int) override {}
    QString attributes(int offset, int *start, int *end) const override {
        *start = qBound(0, offset, characterCount());
        *end = characterCount();
        return {};
    }
};

class Fixture : public QObject {
    QString path;
    QMap<QString, QWidget *> windows;
    QMap<QString, QLineEdit *> fields;
    QMap<QString, QJsonArray> journal;
    int commandId = 0;
    int sequence = 0;
    bool drift = false;
    bool ready = false;

  public:
    explicit Fixture(QString destination) : path(std::move(destination)) {}
    void publish(const QString &reason) {
        QJsonObject values;
        for (auto it = fields.cbegin(); it != fields.cend(); ++it) {
            auto *field = it.value();
            values[it.key()] = QJsonObject{{"text", field->text()},
                                           {"cursor", field->cursorPosition()},
                                           {"selectionStart", field->selectionStart()},
                                           {"selectedText", field->selectedText()},
                                           {"selectionLength", int(field->selectedText().size())},
                                           {"focused", field->hasFocus()},
                                           {"editable", !field->isReadOnly()},
                                           {"keys", journal[it.key()]}};
        }
        QSaveFile file(path);
        if (!file.open(QIODevice::WriteOnly))
            qFatal("Cannot write fixture state");
        file.write(QJsonDocument(QJsonObject{{"command", commandId},
                                             {"sequence", sequence},
                                             {"reason", reason},
                                             {"ready", ready},
                                             {"driftArmed", drift},
                                             {"fields", values}})
                       .toJson());
        if (!file.commit())
            qFatal("Cannot commit fixture state");
    }
    void activate(const QString &key) {
        if (!windows.contains(key))
            qFatal("Unknown owned window");
        windows[key]->raise();
        windows[key]->activateWindow();
    }
    void arrange(const QJsonObject &request) {
        const QString op = request["kind"].toString();
        if (op == "stop") {
            qApp->quit();
            return;
        }
        if (op == "activate")
            activate(request["window"].toString());
        else if (op == "armDrift")
            drift = true;
        else {
            auto *field = fields.value(request["field"].toString(), nullptr);
            if (!field)
                qFatal("Unknown owned field");
            if (op == "focus") {
                field->setFocus(Qt::OtherFocusReason);
                if (request["activate"].toBool())
                    activate(field->window() == windows["a"] ? "a" : "b");
            } else if (op == "select") {
                int start = request["start"].toInt(-1), length = request["length"].toInt(-1);
                if (start < 0 || length < 0 || start + length > field->text().size())
                    qFatal("Invalid actual QLineEdit selection");
                if (length)
                    field->setSelection(start, length);
                else
                    field->setCursorPosition(start);
            } else
                qFatal("Unknown arrangement command");
        }
        int id = request["id"].toInt();
        QTimer::singleShot(150, this, [this, id] {
            commandId = id;
            publish("arranged");
        });
    }
    void add(QVBoxLayout *layout, const QString &key, const QString &name, const QString &text, bool fallback) {
        auto *field = fallback ? static_cast<QLineEdit *>(new KeyboardEntry) : new QLineEdit;
        field->setObjectName(key);
        field->setAccessibleName(name);
        field->setText(text);
        fields[key] = field;
        layout->addWidget(new QLabel(fallback ? "Keyboard input (without EditableText)" : "Native Unicode comparison"));
        layout->addWidget(field);
        field->installEventFilter(this);
        connect(field, &QLineEdit::textChanged, this, [this] { publish("textChanged"); });
        connect(field, &QLineEdit::selectionChanged, this, [this] { publish("selectionChanged"); });
        connect(field, &QLineEdit::cursorPositionChanged, this, [this] { publish("cursorChanged"); });
        if (fallback)
            connect(field, &QLineEdit::textEdited, this, [this] {
                if (drift) {
                    drift = false;
                    fields["bOther"]->setFocus(Qt::OtherFocusReason);
                    activate("b");
                    publish("driftAfterFirstEdit");
                }
            });
    }
    bool eventFilter(QObject *object, QEvent *event) override {
        auto *field = qobject_cast<QLineEdit *>(object);
        if (field && (event->type() == QEvent::KeyPress || event->type() == QEvent::KeyRelease)) {
            auto *key = static_cast<QKeyEvent *>(event);
            journal[field->objectName()].append(
                QJsonObject{{"sequence", ++sequence},
                            {"kind", event->type() == QEvent::KeyPress ? "press" : "release"},
                            {"key", key->key()},
                            {"text", key->text()},
                            {"modifiers", int(key->modifiers())}});
            publish("key");
        } else if (event->type() == QEvent::FocusIn || event->type() == QEvent::FocusOut) {
            QTimer::singleShot(0, this, [this] { publish("focus"); });
        }
        return QObject::eventFilter(object, event);
    }
    void show() {
        auto *a = new QWidget;
        auto *b = new QWidget;
        windows["a"] = a;
        windows["b"] = b;
        a->setWindowTitle("Computer Use v5 Typed A");
        b->setWindowTitle("Computer Use v5 Typed B");
        auto *al = new QVBoxLayout(a);
        auto *bl = new QVBoxLayout(b);
        add(al, "aFallback", "Fallback input", "left|right", true);
        add(al, "aNative", "Native input", "native|text", false);
        add(bl, "bOther", "Other input", "untouched", false);
        a->resize(520, 220);
        a->move(50, 100);
        b->resize(520, 220);
        b->move(720, 100);
        a->show();
        b->show();
        fields["aFallback"]->setSelection(4, 1);
        fields["aFallback"]->setFocus(Qt::OtherFocusReason);
        fields["aNative"]->setSelection(6, 1);
        fields["bOther"]->setFocus(Qt::OtherFocusReason);
        QTimer::singleShot(250, this, [this] {
            ready = true;
            activate("b");
            publish("ready");
        });
    }
};

int main(int argc, char **argv) {
    QApplication app(argc, argv);
    QApplication::setApplicationName("Computer Use v5 Typed Fixture");
    if (argc != 2)
        qFatal("Usage: typed-fixture STATE_JSON");
    QAccessible::installFactory([](const QString &, QObject *object) -> QAccessibleInterface * {
        if (auto *entry = dynamic_cast<KeyboardEntry *>(object))
            return new KeyboardAccessible(entry);
        return nullptr;
    });
    Fixture fixture(QString::fromLocal8Bit(argv[1]));
    QSocketNotifier input(0, QSocketNotifier::Read);
    QObject::connect(&input, &QSocketNotifier::activated, &fixture, [&fixture] {
        std::string line;
        if (!std::getline(std::cin, line)) {
            qApp->quit();
            return;
        }
        QJsonParseError error;
        auto doc = QJsonDocument::fromJson(QByteArray::fromStdString(line), &error);
        if (error.error != QJsonParseError::NoError || !doc.isObject())
            qFatal("Invalid arrangement JSON");
        fixture.arrange(doc.object());
    });
    fixture.show();
    return app.exec();
}
