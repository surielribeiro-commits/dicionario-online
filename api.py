from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import sqlite3
import requests
import re
import unicodedata

app = FastAPI(title="Dicionário de Rimas API")

app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_credentials=True,
    allow_methods=["*"], allow_headers=["*"],
)

ARQUIVO_BANCO = 'dicionario_mestre.db'

BLACKLIST = {
    "calais", "hollywood", "mcdonalds", "facebook", "youtube", 
    "google", "twitter", "instagram", "kaiser", "design", "muié"
}

# --- FUNÇÕES AUXILIARES ---

def remover_acentos(texto):
    return ''.join(c for c in unicodedata.normalize('NFD', texto) if unicodedata.category(c) != 'Mn')

def identificar_tonicidade(palavra):
    """
    Classifica em OXITONA, PAROXITONA ou PROPAROXITONA.
    Corrige o erro de rimar 'Rima' com 'Décima'.
    """
    p = palavra.lower().strip()
    
    # 1. Verifica acentos gráficos
    # Se encontrar acento, conta quantas vogais existem DEPOIS dele.
    # Ex: 'Décima' -> acento no 'é'. Depois tem 'i', 'a' (2 vogais). -> PROPAROXITONA.
    # Ex: 'Café' -> acento no 'é'. Depois tem 0 vogais. -> OXITONA.
    
    vogais_geral = "aeiouáéíóúâêôãõ"
    acento_encontrado = -1
    
    for i, char in enumerate(p):
        if char in "áéíóúâêô": # Acentos tônicos fortes
            acento_encontrado = i
            break
            
    if acento_encontrado != -1:
        resto = p[acento_encontrado+1:]
        # Conta vogais no resto
        num_vogais_pos = len(re.findall(r'[aeiouãõ]', resto))
        
        if num_vogais_pos == 0: return "OXITONA"
        if num_vogais_pos == 1: return "PAROXITONA"
        if num_vogais_pos >= 2: return "PROPAROXITONA" # Aqui cai a Décima, Lágrima, Máxima

    # 2. Sem acento gráfico (Regras Padrão)
    if p.endswith(('r', 'l', 'z', 'x', 'i', 'u', 'im', 'um', 'om', 'un')):
        return "OXITONA"
    
    return "PAROXITONA"

def calcular_pontuacao(palavra_alvo, palavra_candidata, classe_candidata, origem_candidata):
    score = 0
    if origem_candidata: score += 100
    if len(palavra_candidata) <= 2: score -= 10
    return score

def buscar_definicao_online(palavra):
    url = "https://pt.wiktionary.org/w/api.php"
    params = {"action": "parse", "page": palavra, "prop": "text", "formatversion": "2", "format": "json", "redirects": "true"}
    headers = {'User-Agent': 'DicionarioRimasApp/1.0'}
    try:
        resp = requests.get(url, params=params, headers=headers, timeout=5)
        data = resp.json()
        if 'error' in data: return None
        html = data.get('parse', {}).get('text', '')
        match = re.search(r'<ol>(.*?)</ol>', html, re.DOTALL)
        if match:
            item = re.search(r'<li>(.*?)</li>', match.group(1), re.DOTALL)
            if item:
                return re.sub(r'<[^>]+>', '', item.group(1)).strip().replace('\n', ' ')
    except: pass
    return None

def extrair_sufixo_visual(palavra):
    p = palavra.lower().strip()
    if p.endswith('ã'): return 'ã'
    if p.endswith('ãs'): return 'ãs'
    if p.endswith(('ão', 'ãe', 'õe')): return p[-2:]
    if p.endswith(('ãos', 'ães', 'ões')): return p[-3:]
    if p.endswith(('á', 'é', 'í', 'ó', 'ú', 'â', 'ê', 'ô')): return p[-1:] 
    
    if re.search(r'[aeiouáéíóúâêôãõ][rlzxnm]$', p):
        return p[-2:]
        
    vogais = "aeiouáéíóúâêô"
    for i in range(len(p) - 2, -1, -1):
        if p[i] in vogais: return p[i:]
    if len(p) >= 3: return p[-3:]
    return p 

# --- ROTAS ---

@app.get("/")
def home(): return {"status": "Online"}

@app.get("/definicao/{palavra}")
def obter_definicao(palavra: str):
    try:
        conn = sqlite3.connect(ARQUIVO_BANCO)
        cursor = conn.cursor()
        cursor.execute("SELECT id, grafia, classe, definicao FROM palavras WHERE lower(grafia) = ?", (palavra.lower(),))
        res = cursor.fetchone()
        if not res:
            conn.close()
            def_e = buscar_definicao_online(palavra)
            if def_e: return {"palavra": palavra, "classe": "?", "definicao": def_e}
            raise HTTPException(status_code=404, detail="Palavra não encontrada")
        id_p, grafia, classe, def_a = res
        if not def_a or len(def_a) < 5 or "Definição não" in def_a:
            def_on = buscar_definicao_online(grafia)
            if def_on:
                conn = sqlite3.connect(ARQUIVO_BANCO)
                conn.cursor().execute("UPDATE palavras SET definicao = ? WHERE id = ?", (def_on, id_p))
                conn.commit()
                conn.close()
                def_a = def_on
        conn.close()
        return {"palavra": grafia, "classe": classe, "definicao": def_a}
    except Exception as e: raise HTTPException(status_code=500, detail=str(e))

@app.get("/rimar/{palavra}")
def buscar_rimas(palavra: str):
    try:
        conn = sqlite3.connect(ARQUIVO_BANCO)
        cursor = conn.cursor()
        palavra_alvo_low = palavra.lower()
        
        cursor.execute("SELECT ipa, chave_rima, classe, num_silabas, origem FROM palavras WHERE lower(grafia) = ?", (palavra_alvo_low,))
        res = cursor.fetchone()
        if not res:
            conn.close()
            raise HTTPException(status_code=404, detail="Palavra não encontrada")

        ipa_alvo, chave_perf, classe_alvo, silabas, origem_alvo = res
        
        # CALCULA A TONICIDADE DO ALVO (Para filtrar Décima/Lágrima)
        tonicidade_alvo = identificar_tonicidade(palavra_alvo_low)

        candidatos = []
        if chave_perf:
            cursor.execute("SELECT grafia, classe, num_silabas, origem, ipa FROM palavras WHERE chave_rima = ? AND lower(grafia) != ?", (chave_perf, palavra_alvo_low))
            candidatos.extend(cursor.fetchall())
        
        sufixo = extrair_sufixo_visual(palavra_alvo_low)
        if sufixo:
            cursor.execute("SELECT grafia, classe, num_silabas, origem, ipa FROM palavras WHERE grafia LIKE ? AND lower(grafia) != ? LIMIT 3000", ('%' + sufixo, palavra_alvo_low))
            candidatos.extend(cursor.fetchall())

        conn.close()

        resultado_final = []
        vistos = set()

        # VOGAIS PARA CHECAGEM DE DITONGO
        vogais_check = "aeiouáéíóúâêôãõ"

        for grafia, classe, n_silabas, origem, ipa_cand in candidatos:
            g_low = grafia.lower()
            
            if len(grafia) < 2: continue
            if g_low in vistos: continue
            if g_low in BLACKLIST: continue
            if ' ' in grafia or grafia.startswith('-'): continue
            if 'Nome Próprio' in classe and not origem: continue
            
            if palavra_alvo_low.endswith(('u', 'ú')) and g_low.endswith('ou'): continue 
            if palavra_alvo_low.endswith('ou') and g_low.endswith(('u', 'ú')): continue

            # --- FILTRO 1: TONICIDADE (Tchau Décima/Lágrima) ---
            # Se eu busco Paroxítona, só aceito Paroxítona. Se busco Proparoxítona, só aceito Proparoxítona.
            tonicidade_cand = identificar_tonicidade(g_low)
            if tonicidade_alvo != tonicidade_cand:
                continue

            # --- FILTRO 2: ANTI-DITONGO (Tchau Teima/Queima) ---
            # Se a rima visual começa com vogal (ex: "ima"), 
            # verificamos se a letra ANTERIOR a ela também é vogal.
            # Ex: Alvo=Rima (antes do i é R-consoante). Cand=Teima (antes do i é E-vogal).
            # Se forem diferentes, é ditongo vs hiato -> Bloqueia.
            
            if sufixo and sufixo[0] in vogais_check:
                # Acha onde o sufixo começa na palavra candidata
                idx_sufixo = g_low.rfind(sufixo)
                if idx_sufixo > 0:
                    letra_anterior_cand = g_low[idx_sufixo - 1]
                    
                    # Verifica a palavra alvo também
                    idx_sufixo_alvo = palavra_alvo_low.rfind(sufixo)
                    letra_anterior_alvo = "consoante"
                    if idx_sufixo_alvo > 0:
                        if palavra_alvo_low[idx_sufixo_alvo - 1] in vogais_check:
                            letra_anterior_alvo = "vogal"
                    
                    eh_vogal_cand = letra_anterior_cand in vogais_check
                    
                    # Se um tem vogal antes (ditongo) e o outro não, TCHAU!
                    if (letra_anterior_alvo == "vogal" and not eh_vogal_cand) or \
                       (letra_anterior_alvo != "vogal" and eh_vogal_cand):
                       continue
            # ---------------------------------------------------------

            vistos.add(g_low)
            score = calcular_pontuacao(palavra, grafia, classe, origem)
            
            resultado_final.append({
                "palavra": grafia, "silabas": n_silabas, "origem": origem, "score": score, "classe": classe
            })

        resultado_final.sort(key=lambda x: (x['silabas'], -x['score'], x['palavra']))

        return {
            "termo": palavra, "ipa": ipa_alvo, "classe_gramatical": classe_alvo, "origem": origem_alvo,
            "rimas": resultado_final
        }
    except Exception as e:
        print(f"ERRO: {e}")
        raise HTTPException(status_code=500, detail=str(e))