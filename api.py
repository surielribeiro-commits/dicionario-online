from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import sqlite3
import requests
import re

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

def identificar_tonicidade(palavra):
    """
    Define se é OXITONA, PAROXITONA ou PROPAROXITONA.
    Essencial para não misturar 'Cabula' com 'Fécula'.
    """
    p = palavra.lower().strip()
    
    # Vogais que contam como sílabas
    vogais_silabicas = "aeiouãõáéíóúâêô"
    
    # 1. Procura acento gráfico (A Regra de Ouro das Proparoxítonas)
    acento_idx = -1
    for i, char in enumerate(p):
        if char in "áéíóúâêô":
            acento_idx = i
            break
    
    if acento_idx != -1:
        # Se tem acento, contamos quantas vogais existem DEPOIS dele.
        # Ex: Fécula (acento no é). Depois tem 'u', 'a'. Total 2. -> PROPAROXITONA.
        # Ex: Túnel (acento no ú). Depois tem 'e'. Total 1. -> PAROXITONA.
        # Ex: Baú (acento no ú). Depois tem 0. -> OXITONA.
        
        resto = p[acento_idx+1:]
        # Conta vogais no resto (ignorando consoantes)
        num_vogais_pos = 0
        for char in resto:
            if char in vogais_silabicas:
                num_vogais_pos += 1
        
        if num_vogais_pos == 0: return "OXITONA"
        if num_vogais_pos == 1: return "PAROXITONA"
        if num_vogais_pos >= 2: return "PROPAROXITONA" # Bingo!

    # 2. Se não tem acento gráfico, usamos as regras de terminação
    
    # Oxítonas naturais (terminam em R, L, Z, X, I, U, IM, UM, OM)
    # Ex: Mulher, Amor, Tupi, Urubu, Bom
    if p.endswith(('r', 'l', 'z', 'x', 'i', 'u', 'im', 'um', 'om', 'un')):
        return "OXITONA"
    
    # Til no final (Irmã, Manhã) -> Oxítona
    if p.endswith(('ã', 'ãs')):
        return "OXITONA"
        
    # Todo o resto é PAROXÍTONA (Casa, Cabula, Bula, Jovem)
    return "PAROXITONA"

def extrair_sufixo_visual(palavra):
    p = palavra.lower().strip()
    if p.endswith('ã'): return 'ã'
    if p.endswith('ãs'): return 'ãs'
    if p.endswith(('ão', 'ãe', 'õe')): return p[-2:]
    if p.endswith(('ãos', 'ães', 'ões')): return p[-3:]
    if p.endswith(('á', 'é', 'í', 'ó', 'ú', 'â', 'ê', 'ô')): return p[-1:] 
    
    if re.search(r'[aeiouáéíóúâêôãõ][rlzxnm]$', p):
        return p[-2:]
    
    # Para 'Cabula', pega a vogal anterior: 'abula' ou 'bula'
    # Mas o 'limit' do SQL cuida de pegar parecidos.
    vogais = "aeiouáéíóúâêô"
    for i in range(len(p) - 2, -1, -1):
        if p[i] in vogais:
            # Se for ditongo (Queima), pega a anterior tb
            if i > 0 and p[i-1] in vogais: return p[i-1:]
            return p[i:]
            
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
        
        # 1. IDENTIFICA TONICIDADE DO ALVO (Ex: Cabula -> Paroxítona)
        tonicidade_alvo = identificar_tonicidade(palavra_alvo_low)

        candidatos = []
        if chave_perf:
            cursor.execute("SELECT grafia, classe, num_silabas, origem, ipa FROM palavras WHERE chave_rima = ? AND lower(grafia) != ?", (chave_perf, palavra_alvo_low))
            candidatos.extend(cursor.fetchall())
        
        sufixo = extrair_sufixo_visual(palavra_alvo_low)
        if sufixo:
            # Busca ampla visual
            cursor.execute("SELECT grafia, classe, num_silabas, origem, ipa FROM palavras WHERE grafia LIKE ? AND lower(grafia) != ? LIMIT 3000", ('%' + sufixo, palavra_alvo_low))
            candidatos.extend(cursor.fetchall())

        conn.close()

        resultado_final = []
        vistos = set()

        for grafia, classe, n_silabas, origem, ipa_cand in candidatos:
            g_low = grafia.lower()
            
            if len(grafia) < 2: continue
            if g_low in vistos: continue
            if g_low in BLACKLIST: continue
            if ' ' in grafia or grafia.startswith('-'): continue
            if 'Nome Próprio' in classe and not origem: continue
            
            if palavra_alvo_low.endswith(('u', 'ú')) and g_low.endswith('ou'): continue 
            if palavra_alvo_low.endswith('ou') and g_low.endswith(('u', 'ú')): continue

            # --- FILTRO DE TONICIDADE RIGOROSO ---
            # Calcula a tonicidade do candidato (Ex: Fécula -> Proparoxítona)
            tonicidade_cand = identificar_tonicidade(g_low)
            
            # Se forem diferentes, descarta!
            # (Cabula [Parox] != Fécula [Proparox]) -> Tchau!
            if tonicidade_alvo != tonicidade_cand:
                continue
            # -------------------------------------

            # Filtro de Ditongo (Regra do Espelho)
            # Se eu termino em vogal+sufixo (ex: Teima) e o outro não (Rima), ou vice-versa.
            if sufixo and sufixo[0] in "aeiouáéíóúâêôãõ":
                idx_alvo = palavra_alvo_low.rfind(sufixo)
                idx_cand = g_low.rfind(sufixo)
                if idx_alvo > 0 and idx_cand > 0:
                    ant_alvo = palavra_alvo_low[idx_alvo-1] in "aeiouáéíóúâêôãõ"
                    ant_cand = g_low[idx_cand-1] in "aeiouáéíóúâêôãõ"
                    if ant_alvo != ant_cand: continue

            vistos.add(g_low)
            score = calcular_pontuacao(palavra, grafia, classe, origem)
            resultado_final.append({"palavra": grafia, "silabas": n_silabas, "origem": origem, "score": score, "classe": classe})

        resultado_final.sort(key=lambda x: (x['silabas'], -x['score'], x['palavra']))

        return {
            "termo": palavra, "ipa": ipa_alvo, "classe_gramatical": classe_alvo, "origem": origem_alvo,
            "rimas": resultado_final
        }
    except Exception as e:
        print(f"ERRO: {e}")
        raise HTTPException(status_code=500, detail=str(e))