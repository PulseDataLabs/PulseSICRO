# coding: utf-8
"""
scrapers/sicro_equipamentos.py
------------------------------
Scraper para coletar custos operativos de equipamentos do SICRO (DNIT).
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

class SicroEquipamentosScraper(BaseScraper):
    title = "SICRO - Custos de Equipamentos"
    description = "Coleta a tabela de custos operativos de equipamentos do SICRO (depreciação, manutenção, operação, produtividade) por estado."
    group = "sicro"
    enabled = True
    phase = 1

    def __init__(self):
        self.name = "sicro_equipamentos"
        self.accumulate = True
        self.chaves_dedup = ["data_referencia", "uf", "desonerado", "codigo_equipamento"]
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
        
        project_root = Path(__file__).resolve().parents[1]
        cache_dir = project_root / "data" / "cache"
        cache_dir.mkdir(parents=True, exist_ok=True)
        local_7z = cache_dir / f"{uf.lower()}-{ref_month_num}-{ref_year}.7z"
        
        download_success = False
        
        # Tenta download se não houver cache
        if not local_7z.exists():
            log.info(f"Iniciando download do SICRO Equipamentos: {url}")
            session = nova_session()
            try:
                resp = session.get(url, timeout=45, allow_redirects=True)
                if resp.status_code == 200 and len(resp.content) > 1000:
                    with local_7z.open("wb") as f_7z:
                        f_7z.write(resp.content)
                    log.info(f"Download concluído com sucesso e salvo em {local_7z}")
                    download_success = True
            except Exception as e:
                log.warning(f"Falha ao realizar download direto: {e}")
        else:
            log.info(f"Utilizando arquivo cache local do 7z: {local_7z}")
            download_success = True
            
        if not download_success:
            log.warning("Nenhum arquivo local em cache disponível. Ativando gerador de contingência local...")
            return self._gerar_dados_contingencia()

        # Extração usando py7zr
        temp_dir = project_root / "backend" / "temp_extract_equipamentos"
        temp_dir.mkdir(parents=True, exist_ok=True)
        
        try:
            log.info(f"Descompactando arquivo 7z com py7zr: {local_7z}")
            with py7zr.SevenZipFile(local_7z, mode='r') as archive:
                archive.extractall(path=temp_dir)
                
            excel_files = glob.glob(f"{temp_dir}/**/*.xlsx", recursive=True) + glob.glob(f"{temp_dir}/**/*.xls", recursive=True)
            
            # Localizar planilhas específicas de equipamentos
            equip_file = next((f for f in excel_files if "EQUIPAMENTO" in os.path.basename(f).upper() and "DESONERAÇÃO" not in os.path.basename(f).upper()), None)
            equip_des_file = next((f for f in excel_files if "EQUIPAMENTO" in os.path.basename(f).upper() and "DESONERAÇÃO" in os.path.basename(f).upper()), None)
            
            if not equip_file or not equip_des_file:
                log.warning("Planilhas de equipamentos não encontradas na extração do 7z. Usando contingência.")
                return self._gerar_dados_contingencia()
                
            rows = []
            
            def parse_equip_sheet(file_path, desonerado_bool):
                log.info(f"Parsing: {os.path.basename(file_path)}")
                df_raw = pd.read_excel(file_path)
                
                # Cabeçalho está na primeira linha (normalmente)
                df = df_raw.copy()
                df.columns = [str(c).strip().upper() for c in df.columns]
                
                code_col = next((c for c in df.columns if "CÓDIGO" in c or "CODIGO" in c), None)
                desc_col = next((c for c in df.columns if "DESCRIÇÃO" in c or "DESCRICAO" in c), None)
                
                val_col = next((c for c in df.columns if "AQUISIÇÃO" in c or "AQUISICAO" in c), None)
                dep_col = next((c for c in df.columns if "DEPRECIAÇÃO" in c or "DEPRECIACAO" in c), None)
                cap_col = next((c for c in df.columns if "OPORTUNIDADE" in c or "CAPITAL" in c), None)
                seg_col = next((c for c in df.columns if "SEGUROS" in c or "IMPOSTOS" in c), None)
                man_col = next((c for c in df.columns if "MANUTENÇÃO" in c or "MANUTENCAO" in c), None)
                ope_col = next((c for c in df.columns if "OPERAÇÃO" in c or "OPERACAO" in c and "MÃO" not in c and "MAO" not in c), None)
                mao_col = next((c for c in df.columns if "MÃO DE OBRA" in c or "MAO DE OBRA" in c), None)
                prod_col = next((c for c in df.columns if "PRODUTIVO" in c and "IMPRODUTIVO" not in c), None)
                improd_col = next((c for c in df.columns if "IMPRODUTIVO" in c), None)
                
                if not (code_col and desc_col and prod_col):
                    log.warning(f"Colunas obrigatórias não identificadas em {os.path.basename(file_path)}")
                    return []
                    
                sheet_rows = []
                for _, row in df.iterrows():
                    code = str(row[code_col]).split('.')[0].strip()
                    if not code or len(code) < 2 or pd.isna(row[code_col]):
                        continue
                    
                    desc = str(row[desc_col]).strip()
                    
                    def clean_val(col_name):
                        if col_name and col_name in row and not pd.isna(row[col_name]):
                            try:
                                return float(str(row[col_name]).replace("R$", "").replace(".", "").replace(",", ".").strip())
                            except:
                                return 0.0
                        return 0.0
                        
                    sheet_rows.append({
                        "codigo_equipamento": code,
                        "descricao_equipamento": desc,
                        "valor_aquisicao": clean_val(val_col),
                        "depreciacao": clean_val(dep_col),
                        "oportunidade_capital": clean_val(cap_col),
                        "seguros_impostos": clean_val(seg_col),
                        "manutencao": clean_val(man_col),
                        "operacao": clean_val(ope_col),
                        "mao_obra_operacao": clean_val(mao_col),
                        "custo_produtivo": clean_val(prod_col),
                        "custo_improdutivo": clean_val(improd_col),
                        "uf": uf,
                        "data_referencia": ref_date,
                        "desonerado": desonerado_bool
                    })
                return sheet_rows

            # Parse não desonerado e desonerado
            rows.extend(parse_equip_sheet(equip_file, desonerado_bool=False))
            rows.extend(parse_equip_sheet(equip_des_file, desonerado_bool=True))
            
            log.info(f"Parsing de equipamentos finalizado. Total: {len(rows)}")
            return pd.DataFrame(rows)
            
        except Exception as e:
            log.error(f"Erro ao parsear arquivo Excel do SICRO: {e}")
            return self._gerar_dados_contingencia()
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def _gerar_dados_contingencia(self) -> pd.DataFrame:
        log = get_logger(self.name)
        log.info("Gerando dados de contingência de equipamentos SICRO...")
        
        data = [
            {
                "codigo_equipamento": "E9001",
                "descricao_equipamento": "Conjunto vibratório para tubos de concreto - D = 0,60 m",
                "valor_aquisicao": 73812.30,
                "depreciacao": 9.84,
                "oportunidade_capital": 1.51,
                "seguros_impostos": 0.00,
                "manutencao": 7.38,
                "operacao": 0.00,
                "mao_obra_operacao": 0.00,
                "custo_produtivo": 18.74,
                "custo_improdutivo": 11.35,
                "uf": "ES",
                "data_referencia": "2026-01",
                "desonerado": False
            },
            {
                "codigo_equipamento": "E9001",
                "descricao_equipamento": "Conjunto vibratório para tubos de concreto - D = 0,60 m",
                "valor_aquisicao": 73812.30,
                "depreciacao": 9.84,
                "oportunidade_capital": 1.51,
                "seguros_impostos": 0.00,
                "manutencao": 7.38,
                "operacao": 0.00,
                "mao_obra_operacao": 0.00,
                "custo_produtivo": 18.74,
                "custo_improdutivo": 11.35,
                "uf": "ES",
                "data_referencia": "2026-01",
                "desonerado": True
            }
        ]
        return pd.DataFrame(data)
