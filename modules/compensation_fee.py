def get_compensation(df,colname):
    df[colname] = df[colname].str.replace('(?<=\d)\.(?=\d)','', regex=True)
    #replace spaces between numbers
    df[colname] = df[colname].str.replace('(\d)\s+(\d)','', regex=True)
    df[colname] = df[colname].str.replace('(\.\.\.)','', regex=True)
    # replace spaces between numbers in text
    #df['kwoty'] = df['sentence'].str.extract('(zasąd(?:za|ił)(?:\W+\w+){0,8}?\W+na rzecz (?:powoda|powódki|strony powodowej) kwotę [\d+.]*)', expand=True)
    df['kwoty'] = df[colname]str.extract('(zasąd(?:za|ił)(?:\W+\w+){0,20}[\d+.]*)', expand=True)