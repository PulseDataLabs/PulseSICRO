# PulseSICRO

**Inteligência em Custos de Obras de Infraestrutura de Transportes** — dados do SICRO (DNIT) coletados, estruturados, normalizados e entregues para uso institucional.

---

## O problema

O SICRO (Sistema de Custos Referenciais de Obras do DNIT) é o principal referencial para orçamentos de obras de infraestrutura de transportes (rodovias, ferrovias, hidrovias) no Brasil. No entanto, o consumo de seus dados apresenta desafios parecidos com o SINAPI:

- **Estruturas Descentralizadas**: Diferente do SINAPI que centraliza tudo em uma única página, o SICRO organiza os dados em uma árvore aninhada (Região $\rightarrow$ Estado $\rightarrow$ Ano $\rightarrow$ Mês).
- **Formato Proprietário**: Arquivos compactados no formato `.7z`.
- **headers Dinâmicos**: Os cabeçalhos e layouts das planilhas de referência sofrem variações pontuais dependendo da UF e da data.

O **PulseSICRO** resolve isso utilizando uma arquitetura robusta de scrapers dedicados para baixar os arquivos `.7z` do portal do DNIT, descompactá-los, parsear as tabelas Excel em lote, detectar desvios de esquema (schema drift) e exportá-las em arquivos estruturados planos (CSV).

---

## Arquitetura de Diretórios

```
PulseSICRO/
├── scrapers/
│   ├── utils/
│   │   └── base.py             # Classe BaseScraper comum para extração de 7z
│   └── sicro_insumos.py        # Coleta e parsing de insumos do SICRO
├── utils/
│   └── base.py                 # Escrita flat (salvar_csv com deduplicação)
├── data/
│   ├── sicro_insumos.csv       # Base flat estruturada de insumos
│   ├── last_updates.json       # Bounds temporais dos dados flat
│   └── pipeline_status.json    # Status do pipeline
├── requirements.txt            # Dependências Python (incluindo py7zr)
├── resources.yaml              # Definições de templates de URLs
└── run_all.py                  # Orquestrador do pipeline
```

---

## Instalação e Execução

### Requisitos
- Python 3.10+

### Setup do Ambiente
```bash
# 1. Entre na pasta
cd PulseSICRO

# 2. Instale as dependências
pip install -r requirements.txt
```

### Executar o Pipeline
```bash
python run_all.py
```
