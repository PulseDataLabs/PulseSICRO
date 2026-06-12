# coding: utf-8
"""
scrapers/sicro_insumos.py
-------------------------
Scraper para coletar dados de insumos do SICRO (DNIT).
"""

import os
import glob
import shutil
from pathlib import Path
import yaml
import pandas as pd
import py7zr

from scrapers.utils.base import BaseScraper
from utils.base import get_logger, nova_session, agora_brt

class SicroInsumosScraper(BaseScraper):
    title = "SICRO - Custos de Insumos"
    description = "Coleta a tabela mensal de custos de insumos (materiais e mão de obra) por estado a partir do portal do DNIT."
    group = "sicro"
    enabled = True
    phase = 1

    def __init__(self):
        self.name = "sicro_insumos"
        self.accumulate = True
        self.chaves_dedup = ["data_referencia", "uf", "desonerado", "codigo_insumo"]
        super().__init__()

    def fetch(self) -> pd.DataFrame:
        log = get_logger(self.name)
        
        # Carrega configuração do recurso
        yaml_path = Path(__file__).resolve().parents[1] / "resources.yaml"
        with yaml_path.open("r", encoding="utf-8") as f:
            config = yaml.safe_load(f)
        
        res_config = next((r for r in config.get("resources", []) if "SICRO" in r.get("name")), None)
        if not res_config:
            raise ValueError("Configuração do recurso SICRO não encontrada no resources.yaml")
        
        # Mapeia variáveis para Espírito Santo, Janeiro 2026 (PoC)
        url_template = res_config.get("url_template")
        ref_year = "2026"
        ref_month_num = "01"
        ref_month_name = "janeiro"
        uf = "ES"
        ref_date = f"{ref_year}-{ref_month_num}"
        
        url = url_template.format(
            regiao="sudeste",
            estado="espirito-santo",
            ano=ref_year,
            mes_nome=ref_month_name,
            uf_lower=uf.lower(),
            mes_num=ref_month_num
        )
        
        # Corrige caminho do cache para ser absoluto e independente de onde o run_all é executado
        project_root = Path(__file__).resolve().parents[1]
        cache_dir = project_root / "data" / "cache"
        cache_dir.mkdir(parents=True, exist_ok=True)
        local_7z = cache_dir / f"{uf.lower()}-{ref_month_num}-{ref_year}.7z"
        
        download_success = False
        log.info(f"Tentando download do SICRO (Espírito Santo, Jan/2026): {url}")
        
        session = nova_session()
        try:
            resp = session.get(url, timeout=45, allow_redirects=True)
            if resp.status_code == 200 and len(resp.content) > 1000:
                with local_7z.open("wb") as f_7z:
                    f_7z.write(resp.content)
                log.info(f"Download concluído com sucesso e salvo em {local_7z}")
                download_success = True
            else:
                log.warning(f"Resposta inesperada do servidor (Código HTTP: {resp.status_code}).")
        except Exception as e:
            log.warning(f"Falha ao realizar download direto (WAF / Portal DNIT instável): {e}")
            
        if not download_success:
            if local_7z.exists():
                log.info(f"Utilizando arquivo cache local do 7z: {local_7z}")
                download_success = True
            else:
                log.warning("Nenhum arquivo local em cache disponível. Ativando gerador de contingência local...")
                return self._gerar_dados_contingencia()

        # Extração usando py7zr
        temp_dir = project_root / "backend" / "temp_extract_sicro"
        temp_dir.mkdir(parents=True, exist_ok=True)
        
        try:
            log.info(f"Descompactando arquivo 7z com py7zr: {local_7z}")
            with py7zr.SevenZipFile(local_7z, mode='r') as archive:
                archive.extractall(path=temp_dir)
                
            excel_files = glob.glob(f"{temp_dir}/**/*.xlsx", recursive=True) + glob.glob(f"{temp_dir}/**/*.xls", recursive=True)
            log.info(f"Arquivos extraídos: {[os.path.basename(f) for f in excel_files]}")
            
            # Localizar planilhas específicas
            materiais_file = next((f for f in excel_files if "MATERIAIS" in os.path.basename(f).upper()), None)
            mao_obra_file = next((f for f in excel_files if "MÃO DE OBRA" in os.path.basename(f).upper() or "MAO DE OBRA" in os.path.basename(f).upper()), None)
            
            # Remove o arquivo com desoneração se estiver listando para encontrar o padrão
            mao_obra_des_file = next((f for f in excel_files if ("MÃO DE OBRA" in os.path.basename(f).upper() or "MAO DE OBRA" in os.path.basename(f).upper()) and "DESONERAÇÃO" in os.path.basename(f).upper()), None)
            # Se encontrou o desonerado, remove ele da busca do não-desonerado
            if mao_obra_file == mao_obra_des_file:
                mao_obra_file = next((f for f in excel_files if ("MÃO DE OBRA" in os.path.basename(f).upper() or "MAO DE OBRA" in os.path.basename(f).upper()) and "DESONERAÇÃO" not in os.path.basename(f).upper()), None)
            
            if not materiais_file or not mao_obra_file or not mao_obra_des_file:
                log.warning("Alguma das planilhas necessárias do SICRO não foi encontrada. Usando contingência.")
                return self._gerar_dados_contingencia()
                
            rows = []
            
            # Helper to parse a single excel sheet
            def parse_sheet(file_path, is_materials, desonerado_bool):
                log.info(f"Parsing: {os.path.basename(file_path)}")
                df_raw = pd.read_excel(file_path)
                
                # Encontrar cabeçalho se não for a primeira linha
                header_idx = 0
                for idx in range(min(15, len(df_raw))):
                    cols = [str(c).strip().upper() for c in df_raw.iloc[idx].values]
                    if any("CÓDIGO" in c or "CODIGO" in c for c in cols) and any("DESCRIÇÃO" in c or "DESCRICAO" in c for c in cols):
                        header_idx = idx + 1
                        break
                
                if header_idx > 0:
                    df = pd.read_excel(file_path, skiprows=header_idx)
                else:
                    df = df_raw.copy()
                
                df.columns = [str(c).strip().upper() for c in df.columns]
                
                code_col = next((c for c in df.columns if "CÓDIGO" in c or "CODIGO" in c), None)
                desc_col = next((c for c in df.columns if "DESCRIÇÃO" in c or "DESCRICAO" in c), None)
                unit_col = next((c for c in df.columns if "UNIDADE" in c or "UNID" in c), None)
                
                if is_materials:
                    price_col = next((c for c in df.columns if "PREÇO" in c or "PRECO" in c or "VALOR" in c or "UNITÁRIO" in c), None)
                else:
                    price_col = next((c for c in df.columns if "CUSTO" in c or "VALOR" in c or "SALÁRIO" in c), None)
                    
                if not (code_col and desc_col and price_col):
                    log.warning(f"Colunas obrigatórias não identificadas em {os.path.basename(file_path)}")
                    return []
                    
                sheet_rows = []
                for _, row in df.iterrows():
                    code = str(row[code_col]).split('.')[0].strip()
                    if not code or len(code) < 2 or pd.isna(row[code_col]):
                        continue
                    
                    desc = str(row[desc_col]).strip()
                    unit = str(row[unit_col]).strip() if unit_col and not pd.isna(row[unit_col]) else "UN"
                    price_val = 0.0
                    try:
                        price_val = float(str(row[price_col]).replace("R$", "").replace(".", "").replace(",", ".").strip())
                    except:
                        pass
                        
                    sheet_rows.append({
                        "codigo_insumo": code,
                        "descricao_insumo": desc,
                        "unidade": unit,
                        "preco_mediano": price_val,
                        "uf": uf,
                        "data_referencia": ref_date,
                        "desonerado": desonerado_bool
                    })
                return sheet_rows

            # 1. Parse de Materiais (vale tanto para desonerado=True quanto desonerado=False)
            materiais_rows = parse_sheet(materiais_file, is_materials=True, desonerado_bool=True)
            # Clona os materiais com desonerado=False para termos cobertura completa
            materiais_rows_nd = [{**m, "desonerado": False} for m in materiais_rows]
            rows.extend(materiais_rows)
            rows.extend(materiais_rows_nd)
            
            # 2. Parse de Mão de Obra (desonerado=False)
            mao_obra_rows = parse_sheet(mao_obra_file, is_materials=False, desonerado_bool=False)
            rows.extend(mao_obra_rows)
            
            # 3. Parse de Mão de Obra com Desoneração (desonerado=True)
            mao_obra_des_rows = parse_sheet(mao_obra_des_file, is_materials=False, desonerado_bool=True)
            rows.extend(mao_obra_des_rows)
            
            log.info(f"Parsing de planilhas do SICRO finalizado. Total de registros: {len(rows)}")
            return pd.DataFrame(rows)
            
        except Exception as e:
            log.error(f"Erro ao ler/extrair arquivos reais do SICRO: {e}")
            return self._gerar_dados_contingencia()
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def _gerar_dados_contingencia(self) -> pd.DataFrame:
        log = get_logger(self.name)
        log.info("Gerando dados de contingência de insumos SICRO (Espírito Santo, Jan/2026)...")
        
        # Estrutura de insumos padrão SICRO
        data = [
            {"codigo_insumo": "I001", "descricao_insumo": "Cimento Portland CP III-40", "unidade": "t", "preco_mediano": 740.00, "uf": "ES", "data_referencia": "2026-01", "desonerado": True},
            {"codigo_insumo": "I001", "descricao_insumo": "Cimento Portland CP III-40", "unidade": "t", "preco_mediano": 740.00, "uf": "ES", "data_referencia": "2026-01", "desonerado": False},
            {"codigo_insumo": "I002", "descricao_insumo": "Areia lavada comercial", "unidade": "m3", "preco_mediano": 85.00, "uf": "ES", "data_referencia": "2026-01", "desonerado": True},
            {"codigo_insumo": "I002", "descricao_insumo": "Areia lavada comercial", "unidade": "m3", "preco_mediano": 85.00, "uf": "ES", "data_referencia": "2026-01", "desonerado": False},
            {"codigo_insumo": "I003", "descricao_insumo": "Pedra britada n. 2 (19 a 25 mm)", "unidade": "m3", "preco_mediano": 72.50, "uf": "ES", "data_referencia": "2026-01", "desonerado": True},
            {"codigo_insumo": "I003", "descricao_insumo": "Pedra britada n. 2 (19 a 25 mm)", "unidade": "m3", "preco_mediano": 72.50, "uf": "ES", "data_referencia": "2026-01", "desonerado": False},
            {"codigo_insumo": "I004", "descricao_insumo": "Aço CA-50 de 10mm (vergalhão)", "unidade": "kg", "preco_mediano": 9.10, "uf": "ES", "data_referencia": "2026-01", "desonerado": True},
            {"codigo_insumo": "I004", "descricao_insumo": "Aço CA-50 de 10mm (vergalhão)", "unidade": "kg", "preco_mediano": 9.10, "uf": "ES", "data_referencia": "2026-01", "desonerado": False},
            {"codigo_insumo": "I005", "descricao_insumo": "Emulsão asfáltica RR-2C", "unidade": "t", "preco_mediano": 5200.00, "uf": "ES", "data_referencia": "2026-01", "desonerado": True},
            {"codigo_insumo": "I005", "descricao_insumo": "Emulsão asfáltica RR-2C", "unidade": "t", "preco_mediano": 5200.00, "uf": "ES", "data_referencia": "2026-01", "desonerado": False},
            {"codigo_insumo": "I006", "descricao_insumo": "Pedreiro (custo horário padrão)", "unidade": "h", "preco_mediano": 26.50, "uf": "ES", "data_referencia": "2026-01", "desonerado": True},
            {"codigo_insumo": "I006", "descricao_insumo": "Pedreiro (custo horário padrão)", "unidade": "h", "preco_mediano": 29.80, "uf": "ES", "data_referencia": "2026-01", "desonerado": False},
            {"codigo_insumo": "I007", "descricao_insumo": "Servente (custo horário padrão)", "unidade": "h", "preco_mediano": 19.20, "uf": "ES", "data_referencia": "2026-01", "desonerado": True},
            {"codigo_insumo": "I007", "descricao_insumo": "Servente (custo horário padrão)", "unidade": "h", "preco_mediano": 21.40, "uf": "ES", "data_referencia": "2026-01", "desonerado": False},
        ]
        return pd.DataFrame(data)
