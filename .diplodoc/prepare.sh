#!/usr/bin/env bash
#
# Готовит входную папку для сборки документации Diplodoc.
#
# Собирает .yfm-build/ — копию той части репозитория, которая идёт в сайт
# (корневой README.md + docs/), плюс конфиги из .diplodoc/. Раскладка повторяет
# репозиторий намеренно: так относительные ссылки между README.md и docs/*.md
# продолжают разрешаться и на GitHub, и в собранном сайте.
#
# Ссылки на исходники и служебные файлы (src/**, LICENSE, .env.example) в сайт
# попасть не могут — это не Markdown, и в toc.yaml их нет. Поэтому в копиях они
# переписываются в абсолютные URL на GitHub. Оригиналы в репозитории не
# меняются.
#
# Запускается и локально, и в CI:
#   bash .diplodoc/prepare.sh
#   npx --yes @diplodoc/cli@5 build -i .yfm-build -o _site

set -euo pipefail

REPO_URL="${REPO_URL:-https://github.com/nikborovets/gosuslugi-slot-watcher}"
REPO_REF="${REPO_REF:-main}"

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root"

build_dir=".yfm-build"

rm -rf "$build_dir"
mkdir -p "$build_dir/docs"

# README.md → index.md, чтобы Pages отдавали корень раздела без явного имени
# файла: / вместо /README.html и /docs/ вместо /docs/README.html.
cp README.md "$build_dir/index.md"
cp docs/*.md "$build_dir/docs/"
mv "$build_dir/docs/README.md" "$build_dir/docs/index.md"
cp .diplodoc/toc.yaml .diplodoc/.yfm "$build_dir/"

find "$build_dir" -name '*.md' -print0 | xargs -0 perl -i -pe '
    s{\]\(((?:\.\./|docs/)?)README\.md}{](${1}index.md}g;
'

# Путь после необязательного ../ уже отсчитывается от корня репозитория, так что
# ведущие ../ просто отбрасываются. Ссылка на папку требует /tree/, на файл —
# /blob/, отличаем по завершающему слэшу.
find "$build_dir" -name '*.md' -print0 | xargs -0 perl -i -pe '
    BEGIN { ($url, $ref) = @ARGV[0, 1]; splice(@ARGV, 0, 2); }
    s{
        \]\(                                        # начало цели ссылки
        (?:\.\./)*                                  # выход из docs/, если есть
        ((?:src/|LICENSE|\.env\.example)[^)]*)      # путь от корня репозитория
        \)
    }{
        my $path = $1;   # отдельная переменная: проверка ниже сбросила бы $1
        "](" . $url . "/" . ($path =~ m{/$} ? "tree" : "blob") . "/" . $ref . "/" . $path . ")"
    }gex;
' "$REPO_URL" "$REPO_REF"

echo "готово: $build_dir"
