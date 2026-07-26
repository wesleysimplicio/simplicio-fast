# Simplicio Fast

POC de uma memória semântica binária e incremental para agentes de código. Os
arquivos de desenvolvimento continuam normais; o índice `.sfast` é derivado,
versionado e consultado diretamente com `mmap`.

## Hipótese

Em vez de reler e reinterpretar todo o projeto para cada tarefa:

1. Python analisa os fontes uma vez.
2. Símbolos, localizações e hashes são gravados em registros binários.
3. O sistema operacional pagina o snapshot sob demanda com `mmap`.
4. Arquivos sem alteração reaproveitam seus símbolos no próximo build.
5. A alteração final continua sendo feita nos fontes, nunca no snapshot.

## Executar

Não há dependências de runtime.

```bash
PYTHONPATH=src python -m simplicio_fast.cli build .
PYTHONPATH=src python -m simplicio_fast.cli query UserService
PYTHONPATH=src python -m simplicio_fast.cli serve --port 3000
```

## CRUD de usuários

```bash
curl -X POST http://127.0.0.1:3000/users \
  -H 'content-type: application/json' \
  -d '{"name":"Wesley","email":"wesley@example.com"}'

curl http://127.0.0.1:3000/users
curl -X PUT http://127.0.0.1:3000/users/ID \
  -H 'content-type: application/json' \
  -d '{"active":false}'
curl -X DELETE http://127.0.0.1:3000/users/ID
```

O campo `active` representa a segunda etapa da POC: criar o CRUD, alterar o
modelo depois e confirmar que somente o arquivo modificado precisa ser
reanalisado.

## Validar e medir

```bash
PYTHONPATH=src python -m unittest discover -s tests -v
python benchmarks/run.py
```

O benchmark gera 500 módulos temporários e mede:

- consulta tradicional, analisando todas as ASTs a cada execução;
- construção fria do snapshot;
- consultas repetidas via `mmap`;
- reconstrução sem mudanças;
- reconstrução depois de alterar exatamente um arquivo;
- tempo de parede, CPU, pico de RSS e ganho calculado.

O resultado local fica em `benchmarks/results/latest.json`. Ele é ignorado pelo
Git para evitar publicar números de uma máquina como se fossem universais.

## Limites intencionais da POC

- O parser atual cobre Python usando `ast` da biblioteca padrão.
- O índice contém classes e funções; relações entre chamadas entram na próxima fase.
- A consulta percorre registros binários; uma tabela ordenada/hash será a próxima otimização.
- O snapshot é cache descartável. Os fontes permanecem como única verdade.

## Próxima fase, se a hipótese for confirmada

1. Contrato de snapshot canônico e checksums por seção.
2. Índice direto de símbolos e grafo de chamadas/importações.
3. Adaptadores Tree-sitter para TypeScript, Rust e outras linguagens.
4. Mapper produz o snapshot; Runtime oferece consultas.
5. Loop compartilha uma geração imutável entre slots.
6. Agent e Code solicitam apenas o subgrafo relevante e geram patches verificados.
