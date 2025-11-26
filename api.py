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
    """
    Gera a chave de rima visual.
    NOVA REGRA: Se tiver acento, a rima é do acento até o fim.
    Isso resolve Fécula (écula) vs Fábula (ábula).
    """
    p = palavra.lower().strip()
    
    # 1. REGRA DE OURO DA ACENTUAÇÃO (Prioridade Máxima)
    # Procura qualquer vogal acentuada. Se achar, a rima começa ALI.
    # Ex: Fécula -> 'écula'
    # Ex: Célula -> 'élula'
    # Ex: Baú -> 'ú'
    # Ex: Também -> 'ém'
    
    # Percorre a palavra de trás para frente procurando o acento tônico
    acentos = "áéíóúâêô"
    for i in range(len(p) - 1, -1, -1):
        if p[i] in acentos:
            # Achou a tônica marcada! Retorna do acento até o fim.
            return p[i:]

    # 2. Regras Especiais (Til e Ditongos Nasais - se não tiver acento agudo/circunflexo)
    if p.endswith('ã'): return 'ã'
    if p.endswith('ãs'): return 'ãs'
    if p.endswith(('ão', 'ãe', 'õe')): return p[-2:]
    if p.endswith(('ãos', 'ães', 'ões')): return p[-3:]
    
    # 3. Consoantes finais de rima (Amor, Azul, Paz, Ogum, Nanquim)
    if re.search(r'[aeiou][rlzxnm]$', p):
        return p[-2:]
        
    # 4. REGRA DO CAÇA-VOGAL (Para palavras sem acento gráfico - Paroxítonas)
    # Ex: Casa, Rima, Teima
    vogais = "aeiou"
    for i in range(len(p) - 2, -1, -1):
        if p[i] in vogais:
            # Se tiver outra vogal antes, pega ela tb (Queima -> eima)
            if i > 0 and p[i-1] in vogais: return p[i-1:]
            return p[i:]
            
    if len(p) >= 3: return p[-3:]
    return p 

def identificar_tonicidade(palavra):
    p = palavra.lower().strip()
    acento_idx = -1
    for i, char in enumerate(p):
        if char in "áéíóúâêô": 
            acento_idx = i; break
    if acento_idx != -1:
        resto = p[acento_idx+1:]
        num_vogais_pos = len(re.findall(r'[aeiouãõ]', resto))
        if num_vogais_pos == 0: return "OXITONA"
        if num_vogais_pos == 1: return "PAROXITONA"
        if num_vogais_pos >= 2: return "PROPAROXITONA"
    if p.endswith(('r', 'l', 'z', 'x', 'i', 'u', 'im', 'um', 'om', 'un', 'ã', 'ãs')): return "OXITONA"
    return "PAROXITONA"

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
        
        tonicidade_alvo = identificar_tonicidade(palavra_alvo_low)
        
        # AQUI MUDOU: Agora usamos a nova função para gerar o sufixo estrito
        sufixo_alvo = extrair_sufixo_visual(palavra_alvo_low)

        candidatos = []
        # 1. Fonética
        if chave_perf:
            cursor.execute("SELECT grafia, classe, num_silabas, origem FROM palavras WHERE chave_rima = ? AND lower(grafia) != ?", (chave_perf, palavra_alvo_low))
            candidatos.extend(cursor.fetchall())
        
        # 2. Visual (Agora muito mais preciso para proparoxítonas)
        if sufixo_alvo:
            cursor.execute("SELECT grafia, classe, num_silabas, origem FROM palavras WHERE grafia LIKE ? AND lower(grafia) != ? LIMIT 3000", ('%' + sufixo_alvo, palavra_alvo_low))
            candidatos.extend(cursor.fetchall())

        conn.close()

        resultado_final = []
        vistos = set()

        for grafia, classe, n_silabas, origem in candidatos:
            g_low = grafia.lower()
            
            if len(grafia) < 2: continue
            if g_low in vistos: continue
            if g_low in BLACKLIST: continue
            if ' ' in grafia or grafia.startswith('-'): continue
            if 'Nome Próprio' in classe and not origem: continue
            if palavra_alvo_low.endswith(('u', 'ú')) and g_low.endswith('ou'): continue 
            if palavra_alvo_low.endswith('ou') and g_low.endswith(('u', 'ú')): continue

            # --- FILTROS ---
            
            # 1. Tonicidade (Impede que Fécula rime com uma Paroxítona, se houver erro)
            tonicidade_cand = identificar_tonicidade(g_low)
            if tonicidade_alvo != tonicidade_cand: continue

            # 2. Regra do Espelho Visual (Essencial para Fécula vs Célula)
            # O sufixo de Fécula é 'écula'. O de Célula é 'élula'.
            # 'écula' != 'élula', então serão eliminados.
            sufixo_cand = extrair_sufixo_visual(g_low)
            
            # Nota: A busca fonética (IPA) tem prioridade e pula essa checagem se for perfeita.
            # Mas se a busca fonética trouxe "Célula" (por erro de chave no banco), a gente filtra aqui.
            if sufixo_alvo != sufixo_cand:
                # Se os sufixos visuais não batem, só aceitamos se o IPA for idêntico no banco
                # Mas como estamos limpando, vamos ser rigorosos:
                continue

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