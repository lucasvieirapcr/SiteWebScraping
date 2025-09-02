import requests
import time
import pandas as pd
import io
import sys
from datetime import datetime
from flask import Flask, render_template, request, jsonify, send_from_directory
import os

app = Flask(__name__)

# --- CONSTANTES ---
URL_PLANS = "https://portal-assefaz-a7d7f2eebudqhyfx.eastus2-01.azurewebsites.net/api/rede-credenciada/plans"
URL_STATES = "https://portal-assefaz-a7d7f2eebudqhyfx.eastus2-01.azurewebsites.net/api/rede-credenciada/address/states"
URL_CITIES = "https://portal-assefaz-a7d7f2eebudqhyfx.eastus2-01.azurewebsites.net/api/rede-credenciada/address/cities"
URL_PROVIDERS = "https://portal-assefaz-a7d7f2eebudqhyfx.eastus2-01.azurewebsites.net/api/rede-credenciada/prestadores"
OUTPUT_DIR = 'output'

# --- INÍCIO DAS NOVAS ADIÇÕES ---

def get_latest_output_file():
    """
    Verifica o diretório 'output' e retorna o nome do arquivo mais recente.
    Retorna None se o diretório estiver vazio ou não existir.
    """
    try:
        # Garante que o diretório exista para evitar erros na primeira execução
        os.makedirs(OUTPUT_DIR, exist_ok=True)
        
        # Lista todos os arquivos no diretório de saída
        files = os.listdir(OUTPUT_DIR)
        
        # Cria o caminho completo para cada arquivo e filtra para garantir que são arquivos (e não pastas)
        paths = [os.path.join(OUTPUT_DIR, basename) for basename in files if os.path.isfile(os.path.join(OUTPUT_DIR, basename))]
        
        # Se não houver arquivos, retorna None
        if not paths:
            return None
        
        # Encontra o arquivo com o tempo de modificação mais recente
        latest_file_path = max(paths, key=os.path.getctime)
        
        # Retorna apenas o nome do arquivo, não o caminho completo
        return os.path.basename(latest_file_path)
    except Exception as e:
        print(f"Erro ao tentar encontrar o último arquivo: {e}")
        return None

# --- FIM DAS NOVAS ADIÇÕES ---


def get_plan_choices():
    """Fetches the list of available plans from the API."""
    print("Buscando lista de planos disponíveis...")
    try:
        session = requests.Session()
        session.headers.update({
            "Accept": "application/json, text/plain, */*",
            "Origin": "https://www.assefaz.org.br",
            "Referer": "https://www.assefaz.org.br/",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/108.0.0.0 Safari/537.36"
        })
        plans_data = get_data(session, URL_PLANS, payload={"data": {}})

        if not plans_data or 'planos' not in plans_data:
            print("Erro: Não foi possível obter a lista de planos.")
            return []

        choices = [
            {"name": f"{plan.get('BI3_DESCRI')} ({plan.get('BI3_CODIGO')})", "value": plan.get('BI3_CODIGO')}
            for plan in plans_data.get('planos', [])
        ]
        print(f"Sucesso! {len(choices)} planos encontrados.")

        all_plans_option = [{"name": "TODOS OS PLANOS", "value": "ALL"}]
        return all_plans_option + choices

    except Exception as e:
        print(f"Ocorreu um erro ao buscar os planos: {e}")
        return []

def get_data(session, url, payload=None):
    """Performs a POST request and returns JSON data."""
    try:
        response = session.post(url, json=payload or {})
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        print(f"Erro ao fazer requisição para {url}: {e}")
        return None
    except requests.exceptions.JSONDecodeError:
        print(f"Erro: A resposta de {url} não é um JSON válido.")
        return None

def format_address(provider):
    """Formats the address components into a single string."""
    parts = [
        provider.get('endereco', ''),
        f"nº {provider.get('numero', '')}" if provider.get('numero') else '',
        provider.get('complemento', ''),
        provider.get('bairro', ''),
        f"{provider.get('cidade', '')} - {provider.get('estado', '')}",
        f"CEP: {provider.get('cep', '')}" if provider.get('cep') else ''
    ]
    return ', '.join(filter(None, parts))


def iniciar_scraping(target_plan_code, output_filename):
    """Main function that executes the web scraping for one or all plans."""
    old_stdout = sys.stdout
    sys.stdout = captured_output = io.StringIO()

    try:
        print("Iniciando a automação de web scraping da ASSEFAZ...")

        if not output_filename.lower().endswith('.xlsx'):
            output_filename += '.xlsx'

        session = requests.Session()
        session.headers.update({
            "Accept": "application/json, text/plain, */*",
            "Origin": "https://www.assefaz.org.br",
            "Referer": "https://www.assefaz.org.br/",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/108.0.0.0 Safari/537.36"
        })

        print("Buscando listas de estados e planos...")
        states_data = get_data(session, URL_STATES)
        all_plans_response = get_data(session, URL_PLANS, payload={"data": {}})

        if not all_plans_response or 'planos' not in all_plans_response:
            print("Não foi possível obter os dados iniciais de planos. Encerrando.")
            return captured_output.getvalue(), None
        
        all_plans = all_plans_response.get('planos', [])
        all_states = states_data.get('estados', []) if states_data else []

        plans_to_process = []
        if target_plan_code == "ALL":
            print("Opção 'TODOS OS PLANOS' selecionada. A busca será feita em todos os planos.")
            plans_to_process = all_plans
        else:
            selected_plan = next((plan for plan in all_plans if plan.get('BI3_CODIGO') == target_plan_code), None)
            if selected_plan:
                plans_to_process.append(selected_plan)
                print(f"Plano selecionado: {selected_plan.get('BI3_DESCRI')}")
            else:
                print(f"Plano com código '{target_plan_code}' não encontrado. Encerrando.")
                return captured_output.getvalue(), None

        if not plans_to_process:
            print("Nenhum plano válido para processar. Encerrando.")
            return captured_output.getvalue(), None

        all_providers_data = []
        total_states = len(all_states)

        for plan_idx, current_plan in enumerate(plans_to_process):
            plan_code = current_plan.get('BI3_CODIGO')
            plan_desc = current_plan.get('BI3_DESCRI')
            print(f"\n--- Processando Plano {plan_idx + 1}/{len(plans_to_process)}: {plan_desc} ({plan_code}) ---")

            for state_idx, state in enumerate(all_states):
                state_uf = state.get('UF')
                print(f"  -> Buscando em {state_uf} ({state_idx + 1}/{total_states})...")
                time.sleep(1)

                cities_payload = {"uf": state_uf, "tipo": "hosp", "espec": "todos", "plano": plan_code}
                cities_data = get_data(session, URL_CITIES, cities_payload)
                cities = cities_data.get('municipios', []) if cities_data else []
                if not cities:
                    continue

                for city in cities:
                    providers_payload = {"plano": plan_code, "tipo": "hosp", "uf": state_uf, "codMunicipio": city.get('codigoMunicipio'), "bairro": "Todos", "codEspec": "todos"}
                    providers_data = get_data(session, URL_PROVIDERS, providers_payload)
                    providers = providers_data if providers_data else []
                    if not providers:
                        continue

                    for provider in providers:
                        telefones = ' / '.join(filter(None, [provider.get('telefone'), provider.get('telefone1'), provider.get('telefone2')]))
                        all_providers_data.append({
                            'Plano': plan_desc,
                            'Nome Prestador': provider.get('nomePrestador'),
                            'CNPJ': provider.get('codigoPrestador'),
                            'Endereço Completo': format_address(provider),
                            'Endereço': provider.get('endereco'),
                            'Bairro': provider.get('bairro'),
                            'Cidade': provider.get('cidade'),
                            'Estado': provider.get('estado'),
                            'CEP': provider.get('cep'),
                            'Telefone': telefones
                        })

        if not all_providers_data:
            print("\nNenhum prestador encontrado para os critérios selecionados.")
            return captured_output.getvalue(), None

        print(f"\nColeta finalizada. Total de {len(all_providers_data)} registros de prestadores encontrados.")
        print(f"Salvando dados no arquivo '{output_filename}'...")

        df = pd.DataFrame(all_providers_data)
        header_order = ['Plano', 'Nome Prestador', 'CNPJ', 'Endereço Completo', 'Endereço', 'Bairro', 'Cidade', 'Estado', 'CEP', 'Telefone']
        df = df[header_order]
        
        if not os.path.exists(OUTPUT_DIR):
            os.makedirs(OUTPUT_DIR)
        filepath = os.path.join(OUTPUT_DIR, output_filename)
        df.to_excel(filepath, index=False)

        print(f"\nDados salvos com sucesso no arquivo '{output_filename}'!")

    except Exception as e:
        print(f"\nOcorreu um erro inesperado: {e}")
        return captured_output.getvalue(), None
    finally:
        log_messages = captured_output.getvalue()
        sys.stdout = old_stdout

    return log_messages, output_filename


@app.route('/')
def index():
    plan_choices = get_plan_choices()
    # --- ALTERAÇÃO AQUI ---
    # Chama a função para obter o nome do último arquivo e o passa para o template
    latest_file = get_latest_output_file()
    return render_template('index.html', 
                           plan_choices=plan_choices, 
                           now=datetime.now(), 
                           latest_file=latest_file)

@app.route('/about')
def about():
    """Renders the about page."""
    return render_template('about.html')


@app.route('/start-scraping', methods=['POST'])
def start_scraping_route():
    plan_code = request.form.get('plan_code')
    output_filename = request.form.get('output_filename')
    
    log_messages, filename = iniciar_scraping(plan_code, output_filename)
    
    return jsonify({'log': log_messages, 'filename': filename})

@app.route('/download/<path:filename>')
def download_file(filename):
    """Rota para baixar um arquivo específico pelo nome."""
    return send_from_directory(OUTPUT_DIR, filename, as_attachment=True)

# --- NOVA ROTA ADICIONADA ---
@app.route('/download/latest')
def download_latest_file():
    """Rota para encontrar e baixar o arquivo mais recente do diretório de saída."""
    latest_file = get_latest_output_file()
    if latest_file:
        # Se um arquivo for encontrado, usa send_from_directory para enviá-lo
        return send_from_directory(OUTPUT_DIR, latest_file, as_attachment=True)
    # Se nenhum arquivo for encontrado, retorna uma mensagem de erro
    return "Nenhum arquivo encontrado para download.", 404


if __name__ == "__main__":
    app.run(debug=True)