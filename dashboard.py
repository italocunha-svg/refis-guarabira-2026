import streamlit as st
import pandas as pd
import requests
import time  # <--- NOVA BIBLIOTECA ADICIONADA PARA O "MOTOR DE INSISTÊNCIA"
from datetime import date, datetime
from fpdf import FPDF

# ==========================================
# BASE DE DADOS DOS SERVIDORES
# ==========================================
BASE_SERVIDORES = {
    "002151": "AERSON DOS SANTOS TRAJANO",
    "0002883": "ANA LUCIA AMORIM DA COSTA",
    "0023572": "DIOGO BELARMINO GARCIA DE OLIVEIRA",
    "0022042": "FERNANDO ANTONIO MOURA DA COSTA",
    "0021574": "JOSE ROBERTO BATISTA DOS SANTOS",
    "0023571": "LUCIANA ARRUDA PAULA DA FONSECA",
    "0021986": "LUZICLEIDE SERAFIM FELIX DE SOUSA",
    "0000028": "WASHINGTON DE FREITAS SANTOS"
}

# ==========================================
# CACHE INTELIGENTE DO BANCO CENTRAL (COM MOTOR DE INSISTÊNCIA)
# ==========================================
@st.cache_data(ttl=86400, show_spinner=False)
def obter_taxa_bcb_em_cache(codigo_sgs: int, mes_ini: int, ano_ini: int, data_calculo_str: str) -> float:
    data_ini = date(ano_ini, mes_ini, 1)
    dt_ini_str = data_ini.strftime("%d/%m/%Y")
    
    url = f"https://api.bcb.gov.br/dados/serie/bcdata.sgs.{codigo_sgs}/dados?formato=json&dataInicial={dt_ini_str}&dataFinal={data_calculo_str}"
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    }
    
    # Motor de insistência: Tenta até 3 vezes conectar ao Banco Central
    for tentativa in range(3):
        try:
            # Aumentamos o limite de espera para 15 segundos
            resposta = requests.get(url, headers=headers, timeout=15)
            resposta.raise_for_status()
            dados = resposta.json()
            if not dados: return 0.0
            
            fator_acumulado = 1.0
            for mes in dados:
                taxa_mensal = float(mes['valor']) / 100.0
                fator_acumulado *= (1 + taxa_mensal)
            return fator_acumulado - 1.0
            
        except requests.exceptions.Timeout:
            if tentativa == 2: # Se for a última tentativa (índice 2), ele avisa o erro
                st.error(f"⚠️ O servidor do Banco Central (Índice {codigo_sgs}) está muito lento hoje e não respondeu após várias tentativas. Tente novamente em alguns instantes.")
                return 0.0
            time.sleep(2) # Espera 2 segundos antes de "bater na porta" do governo de novo
            
        except Exception as e:
            st.error(f"⚠️ Erro de comunicação com o Banco Central (Índice {codigo_sgs}). O site do governo pode estar fora do ar. Detalhes: {e}")
            return 0.0
            
    return 0.0

# ==========================================
# 1. MOTOR DE CÁLCULO (LINHA DO TEMPO + TETO SELIC UNIVERSAL)
# ==========================================
class SistemaRefisGuarabira:
    def __init__(self, valor_ufr_pb: float):
        self.valor_ufr_pb = valor_ufr_pb

    def _obter_taxa_acumulada_bcb(self, codigo_sgs: int, data_vencimento: date, data_calculo: date) -> float:
        if data_vencimento.month == 12:
            mes_ini = 1
            ano_ini = data_vencimento.year + 1
        else:
            mes_ini = data_vencimento.month + 1
            ano_ini = data_vencimento.year
        
        data_ini = date(ano_ini, mes_ini, 1)
        if data_calculo < data_ini: return 0.0

        data_calculo_str = data_calculo.strftime("%d/%m/%Y")
        return obter_taxa_bcb_em_cache(codigo_sgs, mes_ini, ano_ini, data_calculo_str)

    def processar_calculo_consolidado(self, lista_debitos, data_calculo: date, contribuinte="", cnpj="", mostrar_comparativo=False):
        total_orig = 0.0
        total_corr = 0.0
        total_multa = 0.0
        total_juros = 0.0
        
        detalhamento_txt = ""

        for i, deb in enumerate(lista_debitos):
            desc = deb.get('descricao', f'Débito {i+1}').upper()
            val_orig = deb['valor']
            dt_venc = deb['vencimento']
            
            dias_atraso = (data_calculo - dt_venc).days
            if dias_atraso <= 0:
                detalhamento_txt += f"--- {desc} (Venc: {dt_venc.strftime('%d/%m/%Y')}) ---\nSTATUS: IGNORADO (Ainda não está vencido)\n\n"
                continue

            diferenca_anos = data_calculo.year - dt_venc.year
            diferenca_meses = data_calculo.month - dt_venc.month
            meses_atraso = max(0, (diferenca_anos * 12) + diferenca_meses)

            ano_venc = dt_venc.year
            texto_comparativo = ""
            texto_juros = ""

            # Calcula o Teto Federal (Selic)
            fator_selic = self._obter_taxa_acumulada_bcb(4390, dt_venc, data_calculo)
            encargos_selic = val_orig * fator_selic

            # ==========================================
            # REGRA 1: ATÉ 2023 (CTM ANTIGO)
            # ==========================================
            if ano_venc <= 2023:
                fator_igpm = self._obter_taxa_acumulada_bcb(189, dt_venc, data_calculo)
                val_corr_mun = val_orig * fator_igpm
                juros_mun = val_orig * (0.01 * meses_atraso)
                val_multa = val_orig * 0.20 
                encargos_mun = val_corr_mun + juros_mun
                nome_regra = "CTM Antigo: IGP-M + Juros"
                nome_indice = "IGP-M"
                
            # ==========================================
            # REGRA 2: A PARTIR DE 2024 (NOVA METODOLOGIA)
            # ==========================================
            else:
                fator_ipca = self._obter_taxa_acumulada_bcb(433, dt_venc, data_calculo)
                val_corr_mun = val_orig * fator_ipca
                val_atualizado_mun = val_orig + val_corr_mun
                
                juros_mun = val_atualizado_mun * (0.01 * meses_atraso) 
                perc_multa = min(dias_atraso * 0.00033, 0.20)
                val_multa = val_atualizado_mun * perc_multa 
                encargos_mun = val_corr_mun + juros_mun
                nome_regra = "Atual: IPCA + Juros"
                nome_indice = "IPCA"

            # ==========================================
            # TRAVA DA SELIC E DISCRIMINAÇÃO DOS JUROS
            # ==========================================
            if encargos_mun <= encargos_selic:
                status_trava = f"(Regra {nome_regra})"
                val_correcao = val_corr_mun
                val_juros = juros_mun
                
                if ano_venc <= 2023:
                    texto_juros = f"Juros de Mora: R$ {val_juros:.2f} (1% a.m. sobre o Valor Original)"
                else:
                    texto_juros = f"Juros de Mora: R$ {val_juros:.2f} (1% a.m. sobre o Valor Atualizado)"
            else:
                status_trava = "(Teto STF Aplicado: Limitado à Selic)"
                val_correcao = min(val_corr_mun, encargos_selic)
                val_juros = encargos_selic - val_correcao
                
                texto_juros = f"Juros de Mora: R$ {val_juros:.2f} (Fração complementar da Selic)"
                
                if mostrar_comparativo:
                    texto_comparativo = (
                        f"   >> [DESCARTADO PELO TETO STF]\n"
                        f"   >> {nome_indice} Mun: R$ {val_corr_mun:.2f} | Juros Mun: R$ {juros_mun:.2f}\n"
                        f"   >> Soma Mun: R$ {encargos_mun:.2f} (Superou a Selic: R$ {encargos_selic:.2f})\n"
                    )

            # Soma da linha
            total_deb = val_orig + val_correcao + val_multa + val_juros

            # Consolidação
            total_orig += val_orig
            total_corr += val_correcao
            total_multa += val_multa
            total_juros += val_juros
            
            # Impressão na Memória
            detalhamento_txt += f"--- {desc} (Venc: {dt_venc.strftime('%d/%m/%Y')}) ---\n"
            detalhamento_txt += f"Valor Original: R$ {val_orig:.2f}\n"
            if texto_comparativo:
                detalhamento_txt += texto_comparativo
            detalhamento_txt += f"Correção Monetária: R$ {val_correcao:.2f} {status_trava}\n"
            detalhamento_txt += f"Multa (Metodologia {ano_venc}): R$ {val_multa:.2f}\n"
            detalhamento_txt += f"{texto_juros}\n"
            detalhamento_txt += f"Subtotal do Débito: R$ {total_deb:.2f}\n\n"

        total_geral = total_orig + total_corr + total_multa + total_juros

        if total_geral <= 0:
            raise ValueError("Nenhum débito válido para cálculo inserido.")

        # ==========================================
        # OPÇÕES DE PARCELAMENTO (REFIS 2026)
        # ==========================================
        max_parcelas = 6 if total_geral <= 10000.00 else 10
        opcoes = []
        opcoes_memoria = ""

        for p in range(1, max_parcelas + 1):
            if p == 1: desc_j, desc_m = 1.00, 0.90
            elif 2 <= p <= 6: desc_j, desc_m = 0.80, 0.80
            else: desc_j, desc_m = 0.40, 0.40

            j_desc = total_juros * (1 - desc_j)
            m_desc = total_multa * (1 - desc_m)
            total_pagar = (total_orig + total_corr) + j_desc + m_desc
            val_parcela = total_pagar / p

            if val_parcela >= self.valor_ufr_pb:
                modalidade = "À Vista" if p == 1 else f"{p}x"
                opcoes.append({
                    "Parcelas": modalidade,
                    "Valor Parcela (R$)": round(val_parcela, 2),
                    "Total a Pagar (R$)": round(total_pagar, 2),
                    "Descontos": f"{(desc_j*100):.0f}% Juros | {(desc_m*100):.0f}% Multa"
                })
                opcoes_memoria += f"[{modalidade}] Parcela: R$ {val_parcela:.2f} | Total: R$ {total_pagar:.2f} | Descontos: {(desc_j*100):.0f}% Juros e {(desc_m*100):.0f}% Multa\n"

        # Cabeçalho do Contribuinte
        cabecalho_contribuinte = ""
        if contribuinte or cnpj:
            cabecalho_contribuinte += "=========================================================\n"
            if contribuinte: cabecalho_contribuinte += f"CONTRIBUINTE: {contribuinte.upper()}\n"
            if cnpj: cabecalho_contribuinte += f"CPF/CNPJ: {cnpj}\n"

        memoria_txt = f"""=========================================================
PREFEITURA MUNICIPAL DE GUARABIRA
MEMÓRIA DE CÁLCULO CONSOLIDADA - REFIS 2026
=========================================================
DATA DO ACORDO: {data_calculo.strftime('%d/%m/%Y')}
{cabecalho_contribuinte}=========================================================
1. EVOLUÇÃO DOS DÉBITOS (CTM E TEMA 1.217 STF)
=========================================================
{detalhamento_txt}=========================================================
2. CONSOLIDAÇÃO DA DÍVIDA (SOMA TOTAL)
=========================================================
- Total Original: R$ {total_orig:.2f}
- Total Correção: R$ {total_corr:.2f}
- Total Multas:   R$ {total_multa:.2f}
- Total Juros:    R$ {total_juros:.2f}
-> DÍVIDA TOTAL SEM DESCONTO: R$ {total_geral:.2f}

=========================================================
3. OPÇÕES DE ENQUADRAMENTO - REFIS 2026
=========================================================
{opcoes_memoria}
"""
        return {"opcoes": opcoes, "memoria_txt": memoria_txt}

# ==========================================
# 3. GERAÇÃO DE PDF
# ==========================================
def gerar_pdf(texto_memoria, nome_servidor, matricula):
    pdf = FPDF()
    pdf.set_margins(left=10, top=10, right=10)
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()
    
    try:
        pdf.image("brasao_guarabira.jpg", x=10, y=10, w=25) 
    except RuntimeError:
        pass
        
    pdf.set_y(10) 
    pdf.set_font("Courier", style='I', size=8) 
    texto_emissor = f"Emitido por: {nome_servidor} - Matrícula: {matricula}"
    pdf.cell(0, 5, txt=texto_emissor, ln=True, align='R')
    
    pdf.set_y(40) 
    pdf.set_font("Courier", size=8.5)
    texto_limpo = texto_memoria.encode('latin-1', 'replace').decode('latin-1')
    
    for linha in texto_limpo.split('\n'):
        pdf.cell(190, 4, txt=linha, ln=True)
        
    pdf.ln(15) 
    pdf.set_font("Courier", style='B', size=8.5)
    pdf.cell(0, 4, txt="___________________________________________________", ln=True, align='C')
    pdf.cell(0, 4, txt=nome_servidor, ln=True, align='C')
    pdf.cell(0, 4, txt=f"Matrícula: {matricula}", ln=True, align='C')
    
    return pdf.output(dest='S').encode('latin-1')

# ==========================================
# 4. INTERFACE VISUAL (STREAMLIT)
# ==========================================
st.set_page_config(page_title="REFIS 2026 - Guarabira", layout="wide")

st.title("🏛️ Simulador REFIS 2026 - Guarabira")
st.markdown("Cálculo consolidado com adequação automática ao **Código Tributário** e ao **Teto Selic Universal (STF Tema 1.217)**.")

with st.sidebar:
    st.image("brasao_guarabira.jpg", use_container_width=True)
    st.header("⚙️ Configurações e Emissor")
    matricula_input = st.text_input("Matrícula do Servidor (Obrigatório)")
    dt_calc = st.date_input("Data do Acordo (Hoje)", date.today(), format="DD/MM/YYYY")
    ufr_pb = st.number_input("Valor UFR-PB Atual (R$)", value=73.96)

st.subheader("👤 Dados do Contribuinte (Opcional)")
col_nome, col_doc = st.columns(2)
with col_nome:
    input_nome = st.text_input("Nome / Razão Social")
with col_doc:
    input_doc = st.text_input("CPF / CNPJ")

# === QUADRO INFORMATIVO (DICA UFR-PB) ===
st.info(
    "💡 **DICA RÁPIDA - CONVERSÃO DE ALVARÁ (UFR-PB)**\n\n"
    "Ao inserir os valores na tabela abaixo, utilize as seguintes cotações:\n"
    "* Para valores de **Janeiro de 2024**: Multiplique a quantidade de UFR-PB da tabela por **R$ 65,21**\n"
    "* Para valores de **Janeiro de 2025**: Multiplique a quantidade de UFR-PB da tabela por **R$ 68,38**"
)

st.subheader("📋 1. Informe os Débitos")
st.markdown("💡 Preencha a descrição, valor e data. Clique no **'+'** abaixo da tabela para adicionar mais anos ou débitos.")

if 'df_debitos_novo' not in st.session_state:
    st.session_state.df_debitos_novo = pd.DataFrame(
        [{"Descrição": "Alvará 2023", "Valor Original (R$)": 1000.00, "Data Vencimento": "10/12/2023"},
         {"Descrição": "Alvará 2024", "Valor Original (R$)": 1000.00, "Data Vencimento": "10/12/2024"}]
    )

df_editado = st.data_editor(
    st.session_state.df_debitos_novo,
    num_rows="dynamic",
    column_config={
        "Descrição": st.column_config.TextColumn(help="Nome do débito (Ex: IPTU 2020)"),
        "Valor Original (R$)": st.column_config.NumberColumn(format="%.2f", min_value=0.01),
        "Data Vencimento": st.column_config.TextColumn(
            help="Pode digitar com ou sem barras. (Ex: 31/12/2023 ou 31122023)",
            validate=r"^(0[1-9]|[12][0-9]|3[01])[/\-\.]?(0[1-9]|1[012])[/\-\.]?(19|20)\d\d$"
        )
    },
    use_container_width=True
)

st.write("")
mostrar_comparativo = st.checkbox("🔍 Exibir comparativo detalhado (CTM vs STF) no relatório quando a regra municipal for descartada", value=False)
btn_calcular = st.button("Gerar Cálculo Oficial Consolidado", use_container_width=True, type="primary")

if btn_calcular:
    if not matricula_input:
        st.error("⚠️ Atenção: A matrícula do servidor no menu lateral é obrigatória para emissão.")
    else:
        lista_debitos = []
        erro_data = False
        
        for index, row in df_editado.iterrows():
            desc = row.get('Descrição')
            val = row.get('Valor Original (R$)')
            dt_str = row.get('Data Vencimento')
            
            if pd.notna(val) and pd.notna(dt_str) and str(dt_str).strip() != "":
                dt_str_clean = str(dt_str).strip().replace("/", "").replace("-", "").replace(".", "")
                
                try:
                    dt = datetime.strptime(dt_str_clean, "%d%m%Y").date()
                except ValueError:
                    st.error(f"❌ Erro na data do débito '{desc}'. Certifique-se de digitar uma data válida (ex: 31122023 ou 31/12/2023).")
                    erro_data = True
                    break
                
                if pd.isna(desc) or str(desc).strip() == "":
                    desc = f"Débito {index + 1}"
                    
                lista_debitos.append({'descricao': str(desc), 'valor': float(val), 'vencimento': dt})

        if not erro_data:
            if not lista_debitos:
                st.error("Por favor, preencha ao menos um débito na tabela acima.")
            else:
                try:
                    motor = SistemaRefisGuarabira(valor_ufr_pb=ufr_pb)
                    with st.spinner('Auditando CTM vs Teto STF Universal...'):
                        resultado = motor.processar_calculo_consolidado(lista_debitos, dt_calc, input_nome, input_doc, mostrar_comparativo)
                    
                    st.subheader("💳 2. Opções de Parcelamento (REFIS 2026)")
                    if resultado['opcoes']:
                        df_resumo = pd.DataFrame(resultado['opcoes'])
                        st.dataframe(df_resumo, use_container_width=True, hide_index=True)
                        
                        st.divider()
                        st.subheader("📊 3. Memória de Cálculo Oficial")
                        st.code(resultado['memoria_txt'], language='text')
                        
                        if matricula_input in BASE_SERVIDORES:
                            nome_do_atendente = BASE_SERVIDORES[matricula_input]
                        else:
                            nome_do_atendente = "SERVIDOR NÃO LOCALIZADO"

                        pdf_bytes = gerar_pdf(resultado['memoria_txt'], nome_do_atendente, matricula_input)
                        
                        st.download_button(
                            label="⬇️ Baixar Documento Oficial em PDF",
                            data=pdf_bytes,
                            file_name=f"Termo_Consolidado_Refis_{dt_calc.strftime('%Y%m%d')}.pdf",
                            mime="application/pdf",
                            type="primary"
                        )
                    else:
                        st.error("Nenhuma opção válida encontrada. O valor da parcela ficaria inferior a 1 UFR-PB.")

                except ValueError as e:
                    st.warning(str(e))
